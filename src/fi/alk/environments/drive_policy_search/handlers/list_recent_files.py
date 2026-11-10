def handle(args, db):
    if "folder_id" not in args or not args["folder_id"]:
        raise ToolError("Missing required argument: folder_id")
    folder_id = str(args["folder_id"])
    page_size = int(args.get("page_size", 10))
    page_token = args.get("page_token")
    offset = int(page_token) if page_token and str(page_token).isdigit() else 0

    folder = db.one("SELECT folder_id FROM folders WHERE folder_id = ?", (folder_id,))
    if not folder:
        raise ToolError(f"Folder '{folder_id}' not found.")

    actor_row = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    user_id = actor_row["user_id"] if actor_row else "u_alice"
    user_record = db.one("SELECT role FROM users WHERE user_id = ?", (user_id,))
    is_admin = user_record and user_record["role"] == "administrator"

    if not is_admin:
        perm = db.one("SELECT role FROM permissions WHERE target_type = 'folder' AND target_id = ? AND user_id = ?", (folder_id, user_id))
        if not perm:
            raise ToolError(f"Permission denied: You do not have access to folder '{folder_id}'.")

    files = db.query("SELECT * FROM files WHERE folder_id = ? AND NOT is_deleted ORDER BY modified_at DESC, file_id ASC", (folder_id,))

    accessible_files = []
    for f in files:
        accessible_files.append({
            "file_id": f["file_id"],
            "title": f["title"],
            "folder_id": f["folder_id"],
            "policy_family": f["policy_family"],
            "status": f["status"],
            "version": f["version"],
            "effective_date": f["effective_date"],
            "mime_type": f["mime_type"],
            "modified_at": f["modified_at"]
        })

    total = len(accessible_files)
    paged = accessible_files[offset : offset + page_size]
    next_token = str(offset + len(paged)) if (offset + len(paged)) < total else None

    return {
        "files": paged,
        "total_results": total,
        "next_page_token": next_token
    }
