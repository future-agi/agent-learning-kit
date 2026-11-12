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


def _vkey(d):
    """A version's order: by effective date, then the version as numbers, so 10.0 follows 9.0."""
    return (d["effective_date"], tuple(int(p) if p.isdigit() else 0 for p in str(d["version"]).split(".")))


def _effective(db, family, as_of):
    rows = db.query("SELECT * FROM kb_documents WHERE family = ? AND status IN ('approved', 'superseded') AND effective_date <= ?", [family, as_of])
    return max(rows, key=_vkey) if rows else None


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
    e = _employee(db, args.get("employee_id"))
    family = args.get("family")
    if not family or not isinstance(family, str):
        raise ToolError("family is required.", category="invalid_input")
    any_doc = db.one("SELECT * FROM kb_documents WHERE family = ?", [family])
    if any_doc is None:
        raise ToolError(f"No document family {family!r}.", category="not_found")
    if any_doc["collection_id"] not in _readable(db, e):
        raise ToolError("This document family is not available to this employee's role.", category="permission_denied")
    as_of = args.get("as_of_date") or _now(db)[:10]
    if not isinstance(as_of, str) or len(as_of) != 10:
        raise ToolError("as_of_date must be YYYY-MM-DD.", category="invalid_input")
    d = _effective(db, family, as_of)
    if d is None:
        raise ToolError(f"No version of {family!r} was in effect on {as_of}.", category="not_found")
    drafts = [x["drive_file_id"] for x in db.query("SELECT * FROM kb_documents WHERE family = ? AND status = 'draft'", [family])]
    out = _meta(db, d)
    out.update({"as_of_date": as_of, "drafts_not_in_effect": drafts,
                "passages": [{"passage_id": p["passage_id"], "section": p["section"], "heading": p["heading"]} for p in db.query("SELECT * FROM kb_passages WHERE drive_file_id = ? ORDER BY section", [d["drive_file_id"]])]})
    return out
