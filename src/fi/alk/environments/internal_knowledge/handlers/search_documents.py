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
    import re
    e = _employee(db, args.get("employee_id"))
    query = args.get("query")
    if not query or not isinstance(query, str):
        raise ToolError("query is required.", category="invalid_input")
    coll = args.get("collection_id")
    readable = _readable(db, e)
    if coll and coll not in readable:
        raise ToolError(f"Collection {coll!r} is not available to this employee's role.", category="permission_denied")
    wide = bool(args.get("include_superseded"))
    stop = {"the", "and", "for", "per", "are", "our", "what", "how", "can", "does", "with", "from", "this", "that", "much", "many", "now", "still", "any", "you", "who", "get", "policy", "current"}
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2 and w not in stop]
    if not words:
        raise ToolError("query needs at least one meaningful word of three or more letters.", category="invalid_input")
    hits = []
    for d in db.query("SELECT * FROM kb_documents ORDER BY drive_file_id"):
        if d["collection_id"] not in readable or (coll and d["collection_id"] != coll):
            continue
        if not wide and d["status"] != "approved":
            continue
        ps = db.query("SELECT * FROM kb_passages WHERE drive_file_id = ? ORDER BY section", [d["drive_file_id"]])
        def hit(w, text):
            toks = re.findall(r"[a-z0-9]+", text.lower())
            return any(t == w or (len(w) >= 4 and t.startswith(w)) or (len(t) >= 4 and w.startswith(t)) for t in toks)
        title_score = sum(hit(w, d["title"] + " " + d["family"].replace("_", " ")) for w in words)
        matched = []
        for p in ps:
            s = sum(hit(w, p["heading"] + " " + p["text"]) for w in words)
            if s:
                matched.append((s, p))
        score = title_score * 2 + sum(s for s, _ in matched)
        if score == 0:
            continue
        hits.append((score, d, [p for _, p in sorted(matched, key=lambda x: -x[0])]))
    hits.sort(key=lambda h: (-h[0], h[1]["drive_file_id"]))
    results = []
    for score, d, ps in hits[:10]:
        m = _meta(db, d)
        m["passages"] = [{"passage_id": p["passage_id"], "section": p["section"], "heading": p["heading"], "snippet": p["text"][:160]} for p in ps]
        results.append(m)
    return {"query": query, "results": results, "count": len(results), "include_superseded": wide}
