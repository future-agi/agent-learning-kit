def handle(args, db):
    database_id = args.get("database_id")
    if not database_id:
        raise ToolError("Missing required parameter 'database_id'.")
    db_row = db.one("SELECT * FROM databases WHERE database_id = ?", (database_id,))
    if db_row is None:
        raise ToolError(f"Database '{database_id}' not found.")
    if not db_row["allowed_access"]:
        raise ToolError(f"Access denied to database '{database_id}'.")

    source_page_id = args.get("source_page_id")
    owner_id = args.get("owner_id")
    status = args.get("status")
    limit = args.get("limit", 50)
    cursor = args.get("cursor")

    offset = 0
    if cursor:
        try:
            offset = int(cursor)
        except Exception:
            offset = 0

    sql = "SELECT action_id, database_id, title, owner_id, due_date, status, source_page_id, external_key FROM action_records WHERE database_id = ?"
    params = [database_id]
    if source_page_id:
        sql += " AND source_page_id = ?"
        params.append(source_page_id)
    if owner_id:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    if status:
        sql += " AND status = ?"
        params.append(status)

    sql += " ORDER BY action_id ASC"
    rows = db.query(sql, params)
    paged = rows[offset:offset + limit]
    next_cur = str(offset + limit) if offset + limit < len(rows) else None

    results = []
    for r in paged:
        results.append({
            "action_id": r["action_id"],
            "database_id": r["database_id"],
            "title": r["title"],
            "owner_id": r["owner_id"],
            "due_date": r["due_date"],
            "status": r["status"],
            "source_page_id": r["source_page_id"],
            "external_key": r["external_key"]
        })
    return {"results": results, "next_cursor": next_cur}