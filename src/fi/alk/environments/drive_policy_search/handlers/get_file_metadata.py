def handle(args, db):
    if "file_id" not in args or not args["file_id"]:
        raise ToolError("Missing required argument: file_id")
    file_id = str(args["file_id"])

    file_row = db.one("SELECT * FROM files WHERE file_id = ?", (file_id,))
    if not file_row or file_row["is_deleted"]:
        raise ToolError(f"File '{file_id}' not found or has been deleted.")

    actor_row = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    user_id = actor_row["user_id"] if actor_row else "u_alice"
    user_record = db.one("SELECT role FROM users WHERE user_id = ?", (user_id,))
    is_admin = user_record and user_record["role"] == "administrator"

    if not is_admin:
        perm = db.one("SELECT role FROM permissions WHERE target_type = 'folder' AND target_id = ? AND user_id = ?", (file_row["folder_id"], user_id))
        file_perm = db.one("SELECT role FROM permissions WHERE target_type = 'file' AND target_id = ? AND user_id = ?", (file_id, user_id))
        if not perm and not file_perm:
            raise ToolError(f"Permission denied: You do not have access to file '{file_id}'.")

    return {
        "file_id": file_row["file_id"],
        "title": file_row["title"],
        "folder_id": file_row["folder_id"],
        "policy_family": file_row["policy_family"],
        "status": file_row["status"],
        "version": file_row["version"],
        "effective_date": file_row["effective_date"],
        "mime_type": file_row["mime_type"],
        "created_at": file_row["created_at"],
        "modified_at": file_row["modified_at"],
        "owner_id": file_row["owner_id"]
    }
