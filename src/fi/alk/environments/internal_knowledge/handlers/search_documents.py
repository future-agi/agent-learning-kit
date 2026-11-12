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
        # A restricted collection's text is read through fetch_passage, where the read is the
        # employee's own: a search never carries it, so a search result can never leak it.
        restricted = db.one("SELECT 1 FROM drive_folders f JOIN collections c ON c.drive_folder_id = f.folder_id WHERE c.collection_id = ? AND f.access_level = 'restricted'", [d["collection_id"]]) is not None
        m["passages"] = [dict({"passage_id": p["passage_id"], "section": p["section"], "heading": p["heading"]}, **({} if restricted else {"snippet": p["text"][:160]})) for p in ps]
        results.append(m)
    return {"query": query, "results": results, "count": len(results), "include_superseded": wide}
