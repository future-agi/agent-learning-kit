def handle(args, db):
    query = args.get("query")
    if not query:
        raise ToolError("Missing required parameter 'query'.")
    filter_type = args.get("filter_type", "all")

    results = []
    q_str = f"%{query.lower()}%"

    if filter_type in ("all", "page"):
        pages = db.query("SELECT page_id, title, created_time, archived, allowed_access FROM pages WHERE allowed_access = TRUE AND (LOWER(title) LIKE ? OR LOWER(content_markdown) LIKE ?)", (q_str, q_str))
        for p in pages:
            results.append({"id": p["page_id"], "type": "page", "title": p["title"], "archived": bool(p["archived"])})

    if filter_type in ("all", "database"):
        dbs = db.query("SELECT database_id, title, allowed_access FROM databases WHERE allowed_access = TRUE AND LOWER(title) LIKE ?", (q_str,))
        for d in dbs:
            results.append({"id": d["database_id"], "type": "database", "title": d["title"]})

    return {"results": results}