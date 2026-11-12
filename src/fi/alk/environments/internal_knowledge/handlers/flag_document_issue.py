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


def _readable(db, e):
    return {r["collection_id"] for r in db.query("SELECT collection_id FROM collection_access WHERE role = ?", [e["role"]])}


def _doc(db, e, fid):
    if not fid or not isinstance(fid, str):
        raise ToolError("drive_file_id is required.", category="invalid_input")
    d = db.one("SELECT * FROM kb_documents WHERE drive_file_id = ?", [fid])
    if d is None:
        raise ToolError(f"Document {fid!r} not found in the corpus.", category="not_found")
    if d["collection_id"] not in _readable(db, e):
        raise ToolError("This document is not available to this employee's role.", category="permission_denied")
    return d


def _handle(args, db):
    e = _employee(db, args.get("employee_id"))
    d = _doc(db, e, args.get("drive_file_id"))
    kind, details, key = args.get("issue_type"), args.get("details"), args.get("request_key")
    if kind not in ("outdated", "conflict", "error", "broken_link", "missing_section"):
        raise ToolError("issue_type must be one of outdated, conflict, error, broken_link, missing_section.", category="invalid_input")
    if not details or not isinstance(details, str):
        raise ToolError("details are required.", category="invalid_input")
    if not key or not isinstance(key, str):
        raise ToolError("request_key is required.", category="invalid_input")
    # A retry with the same key returns the issue it raised, and only that one: the same key from
    # another reporter, or about another document, kind or problem, is a different report.
    replay = db.one("SELECT * FROM document_issues WHERE request_key = ?", [key])
    if replay:
        if (replay["reporter_id"], replay["drive_file_id"], replay["issue_type"], replay["details"]) != (e["employee_id"], d["drive_file_id"], kind, details):
            raise ToolError(f"request_key {key!r} was already used for a different report; use a new key for a new report.", category="conflict")
        return {"issue_id": replay["issue_id"], "owner_id": replay["owner_id"], "status": replay["status"], "idempotent": True}
    # The same reporter already has this kind of problem open on this document: say so instead of
    # raising a second issue, without passing it off as a replay of this call.
    open_one = db.one("SELECT * FROM document_issues WHERE drive_file_id = ? AND issue_type = ? AND reporter_id = ? AND status = 'open'", [d["drive_file_id"], kind, e["employee_id"]])
    if open_one:
        return {"issue_id": open_one["issue_id"], "owner_id": open_one["owner_id"], "status": open_one["status"], "already_reported": True, "idempotent": False}
    n = db.one("SELECT COUNT(*) AS n FROM document_issues")["n"] + 101
    while db.one("SELECT 1 FROM document_issues WHERE issue_id = ?", [f"DI-{n}"]):
        n += 1
    iid = f"DI-{n}"
    db.execute("INSERT INTO document_issues (issue_id, drive_file_id, reporter_id, issue_type, details, owner_id, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
               [iid, d["drive_file_id"], e["employee_id"], kind, details, d["owner_id"], key, _now(db)])
    return {"issue_id": iid, "owner_id": d["owner_id"], "status": "open", "idempotent": False}


_WRITES = ("document_issues",)

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
