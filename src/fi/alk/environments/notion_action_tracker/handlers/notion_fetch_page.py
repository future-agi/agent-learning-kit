def handle(args, db):
    page_id = args.get("page_id")
    if not page_id:
        raise ToolError("Missing required parameter 'page_id'.")
    page = db.one("SELECT * FROM pages WHERE page_id = ?", (page_id,))
    if page is None:
        raise ToolError(f"Page '{page_id}' not found.")
    if not page["allowed_access"]:
        raise ToolError(f"Access denied to page '{page_id}'.")
    return {
        "page_id": page["page_id"],
        "title": page["title"],
        "content_markdown": page["content_markdown"],
        "created_time": page["created_time"],
        "timezone": page["timezone"],
        "archived": bool(page["archived"]),
        "allowed_access": bool(page["allowed_access"])
    }