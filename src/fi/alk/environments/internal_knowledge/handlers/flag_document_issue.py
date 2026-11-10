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


def handle(args, db):
    e = _employee(db, args.get("employee_id"))
    d = _doc(db, e, args.get("drive_file_id"))
    kind, details, key = args.get("issue_type"), args.get("details"), args.get("request_key")
    if kind not in ("outdated", "conflict", "error", "broken_link", "missing_section"):
        raise ToolError("issue_type must be one of outdated, conflict, error, broken_link, missing_section.", category="invalid_input")
    if not details or not isinstance(details, str):
        raise ToolError("details are required.", category="invalid_input")
    if not key or not isinstance(key, str):
        raise ToolError("request_key is required.", category="invalid_input")
    existing = db.one("SELECT * FROM document_issues WHERE request_key = ?", [key]) or db.one(
        "SELECT * FROM document_issues WHERE drive_file_id = ? AND issue_type = ? AND reporter_id = ? AND status = 'open'", [d["drive_file_id"], kind, e["employee_id"]])
    if existing:
        return {"issue_id": existing["issue_id"], "owner_id": existing["owner_id"], "status": existing["status"], "idempotent": True}
    n = db.one("SELECT COUNT(*) AS n FROM document_issues")["n"] + 101
    while db.one("SELECT 1 FROM document_issues WHERE issue_id = ?", [f"DI-{n}"]):
        n += 1
    iid = f"DI-{n}"
    db.execute("INSERT INTO document_issues (issue_id, drive_file_id, reporter_id, issue_type, details, owner_id, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
               [iid, d["drive_file_id"], e["employee_id"], kind, details, d["owner_id"], key, _now(db)])
    return {"issue_id": iid, "owner_id": d["owner_id"], "status": "open", "idempotent": False}
