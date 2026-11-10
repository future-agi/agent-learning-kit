def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T09:00:00Z"


def _employee(db, employee_id):
    if not employee_id or not isinstance(employee_id, str):
        raise ToolError("employee_id is required: the employee asking.", category="invalid_input")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [employee_id])
    if e is None:
        raise ToolError(f"Employee {employee_id!r} not found.", category="not_found")
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


def _effective(db, family, as_of):
    rows = db.query("SELECT * FROM kb_documents WHERE family = ? AND status IN ('approved', 'superseded') AND effective_date <= ? ORDER BY effective_date DESC, version DESC", [family, as_of])
    return rows[0] if rows else None


def _meta(db, d):
    today = _now(db)[:10]
    eff = _effective(db, d["family"], today)
    return {
        "drive_file_id": d["drive_file_id"], "title": d["title"], "family": d["family"],
        "collection_id": d["collection_id"], "version": d["version"], "status": d["status"],
        "effective_date": d["effective_date"], "owner_id": d["owner_id"],
        "in_effect": bool(eff and eff["drive_file_id"] == d["drive_file_id"]),
        "effective_version_file_id": eff["drive_file_id"] if eff else None,
        "readable_text": d["mime_type"] == "text/markdown",
    }


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
    existing = db.one("SELECT * FROM knowledge_requests WHERE request_key = ?", [key])
    if existing:
        return {"request_id": existing["request_id"], "status": existing["status"], "assigned_to": existing["assigned_to"], "idempotent": True}
    c = db.one("SELECT * FROM collections WHERE collection_id = ?", [coll])
    if c is None:
        raise ToolError(f"Collection {coll!r} not found.", category="not_found")
    if coll not in _readable(db, e):
        raise ToolError(f"Collection {coll!r} is not available to this employee's role.", category="permission_denied")
    n = db.one("SELECT COUNT(*) AS n FROM knowledge_requests")["n"] + 101
    while db.one("SELECT 1 FROM knowledge_requests WHERE request_id = ?", [f"KR-{n}"]):
        n += 1
    rid, now = f"KR-{n}", _now(db)
    db.execute("INSERT INTO knowledge_requests (request_id, requester_id, question, collection_id, assigned_to, status, request_key, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, '', ?, ?)",
               [rid, e["employee_id"], question, coll, c["owner_id"], key, now, now])
    due = db.one("SELECT value FROM environment_policy WHERE name = 'request_due_days'")
    return {"request_id": rid, "status": "open", "assigned_to": c["owner_id"], "due_in_days": json.loads(due["value"]) if due else 5, "idempotent": False}
