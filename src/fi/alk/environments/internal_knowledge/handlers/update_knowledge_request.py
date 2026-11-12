def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T09:00:00Z"


def _employee(db, employee_id):
    if not employee_id or not isinstance(employee_id, str):
        raise ToolError("employee_id is required: the employee asking.", category="invalid_input")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [employee_id])
    if e is None:
        raise ToolError(f"Employee {employee_id!r} not found.", category="not_found")
    # The session says who is in the conversation. The employee_id the agent passes is checked
    # against it and never grants anything by itself: naming a People Ops reader does not unlock
    # the restricted collection, and naming a requester does not open their request.
    caller = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    if caller is None or not caller["employee_id"]:
        raise ToolError("No employee is signed in to this conversation; the desk acts only for the signed-in employee.", category="permission_denied")
    if caller["employee_id"] != employee_id:
        raise ToolError(f"Employee {employee_id} is not the employee signed in to this conversation; the desk answers and acts only for the signed-in employee.", category="permission_denied")
    if e["status"] != "active":
        raise ToolError(f"Employee {employee_id} is not active; the knowledge desk serves active employees only.", category="permission_denied")
    return e


def _handle(args, db):
    e = _employee(db, args.get("employee_id"))
    rid = args.get("request_id")
    note, status = args.get("note"), args.get("status")
    if not rid or not isinstance(rid, str):
        raise ToolError("request_id is required.", category="invalid_input")
    if not note and not status:
        raise ToolError("Give a note to add, or status 'withdrawn'.", category="invalid_input")
    if status and status != "withdrawn":
        raise ToolError("The only status an employee may set is 'withdrawn'.", category="invalid_input")
    r = db.one("SELECT * FROM knowledge_requests WHERE request_id = ?", [rid])
    if r is None:
        raise ToolError(f"Knowledge request {rid!r} not found.", category="not_found")
    if r["requester_id"] != e["employee_id"]:
        raise ToolError("Only the requester may change a knowledge request.", category="permission_denied")
    if r["status"] in ("closed", "withdrawn"):
        raise ToolError(f"Knowledge request {rid} is {r['status']} and cannot be changed; open a new one.", category="conflict")
    notes = r["notes"] + ("; " if r["notes"] and note else "") + (note or "")
    new_status = status or r["status"]
    db.execute("UPDATE knowledge_requests SET notes = ?, status = ?, updated_at = ? WHERE request_id = ?", [notes, new_status, _now(db), rid])
    return {"request_id": rid, "status": new_status, "notes": notes}


_WRITES = ("knowledge_requests",)

def _all_or_nothing(db, run):
    """Run the call so that a failed call changes nothing. A handler gets no transaction of its own,
    so every table this tool writes is held first and put back if the call raises, whether a write
    failed partway or the call refused after writing. Only a fault the world simulates on purpose
    (an error marked keep_writes: a rival booking, a commit whose reply was lost, a fault used up)
    keeps what it wrote."""
    held = {table: db.query(f"SELECT * FROM {table}") for table in _WRITES}
    try:
        return run()
    except Exception as failed:
        if not getattr(failed, "keep_writes", False):
            for table, rows in held.items():
                if _same(db.query(f"SELECT * FROM {table}"), rows):
                    continue
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    cols = list(row)
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
        raise

def _same(rows, held):
    """Whether a table still holds exactly the rows it held, in any order: only a table the call
    changed is rewritten."""
    import json

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)

    return sorted(map(key, rows)) == sorted(map(key, held))


def handle(args, db):
    return _all_or_nothing(db, lambda: _handle(args, db))
