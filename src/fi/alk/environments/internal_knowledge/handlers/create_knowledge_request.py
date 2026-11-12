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


def handle(args, db):
    import json
    e = _employee(db, args.get("employee_id"))
    question, coll, key = args.get("question"), args.get("collection_id"), args.get("request_key")
    if not question or not isinstance(question, str):
        raise ToolError("question is required.", category="invalid_input")
    if not key or not isinstance(key, str):
        raise ToolError("request_key is required so a retry does not open a second request.", category="invalid_input")
    if args.get("confirmed") is not True:
        raise ToolError("The employee must agree to open a knowledge request first (confirmed=true).", category="policy_blocked")
    c = db.one("SELECT * FROM collections WHERE collection_id = ?", [coll])
    if c is None:
        raise ToolError(f"Collection {coll!r} not found.", category="not_found")
    if coll not in _readable(db, e):
        raise ToolError(f"Collection {coll!r} is not available to this employee's role.", category="permission_denied")
    # A retry with the same key returns the request it opened, and only that one: the same key from
    # another employee, or for another question or collection, is a different request.
    existing = db.one("SELECT * FROM knowledge_requests WHERE request_key = ?", [key])
    if existing:
        if (existing["requester_id"], existing["question"], existing["collection_id"]) != (e["employee_id"], question, coll):
            raise ToolError(f"request_key {key!r} was already used for a different request; use a new key for a new request.", category="conflict")
        return {"request_id": existing["request_id"], "status": existing["status"], "assigned_to": existing["assigned_to"], "idempotent": True}
    n = db.one("SELECT COUNT(*) AS n FROM knowledge_requests")["n"] + 101
    while db.one("SELECT 1 FROM knowledge_requests WHERE request_id = ?", [f"KR-{n}"]):
        n += 1
    rid, now = f"KR-{n}", _now(db)
    db.execute("INSERT INTO knowledge_requests (request_id, requester_id, question, collection_id, assigned_to, status, request_key, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, '', ?, ?)",
               [rid, e["employee_id"], question, coll, c["owner_id"], key, now, now])
    due = db.one("SELECT value FROM environment_policy WHERE name = 'request_due_days'")
    return {"request_id": rid, "status": "open", "assigned_to": c["owner_id"], "due_in_days": json.loads(due["value"]) if due else 5, "idempotent": False}
