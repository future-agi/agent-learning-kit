_HR_ROLES = ("hr_business_partner", "hr_specialist", "hr_generalist")


def _caller(db, claimed=None):
    """The person signed in to this conversation, from the session and never from an argument. An
    actor the agent names must be that person: naming someone is not being them."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    who = None
    if row and row["employee_id"]:
        who = db.one("SELECT * FROM employees WHERE employee_id = ?", (row["employee_id"],))
    if not who:
        raise ToolError("Permission denied: nobody is signed in to this conversation.")
    if claimed and claimed != who["employee_id"]:
        raise ToolError(f"Permission denied: {claimed} is not the person signed in to this conversation ({who['employee_id']}).")
    return who


def _acts_for(db, caller, employee_id):
    """Whether the caller may change `employee_id`'s leave or cases: their own, or as the HR partner
    the world names for that employee's region (the approval matrix gives the partner the exceptions)."""
    if caller["employee_id"] == employee_id:
        return True
    return db.one(
        "SELECT 1 AS ok FROM hr_partners p JOIN employees e ON e.region = p.region WHERE e.employee_id = ? AND p.employee_id = ?",
        (employee_id, caller["employee_id"]),
    ) is not None


def _handle(args, db):
    case_id = args.get("case_id")
    employee_id = args.get("employee_id")
    note = args.get("note")
    if not case_id or not employee_id or not note:
        raise ToolError("case_id, employee_id and note are required.")
    case = db.one("SELECT * FROM hr_cases WHERE case_id = ?", (case_id,))
    if not case:
        raise ToolError(f"HR case {case_id} not found.")
    if case["employee_id"] != employee_id:
        raise ToolError(f"Permission denied: case {case_id} is not {employee_id}'s.")
    caller = _caller(db)
    if not _acts_for(db, caller, employee_id):
        raise ToolError(f"Permission denied: {caller['employee_id']} cannot add notes to {employee_id}'s case {case_id}.")
    if case["status"] == "resolved":
        raise ToolError(f"Case {case_id} is resolved; open a new case instead.")
    now = db.one("SELECT now FROM clock")["now"]
    notes = ((case["notes"] or "") + f" | {caller['employee_id']}: {note}").strip(" |")
    db.execute("UPDATE hr_cases SET notes = ?, updated_at = ? WHERE case_id = ?", (notes, now, case_id))
    return {"case_id": case_id, "status": case["status"], "updated_at": now, "notes": notes}


_WRITES = ("hr_cases",)

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
