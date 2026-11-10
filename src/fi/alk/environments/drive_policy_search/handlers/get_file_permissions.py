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

    perms = db.query(
        "SELECT permission_id, target_type, target_id, user_id, role FROM permissions WHERE (target_type = 'file' AND target_id = ?) OR (target_type = 'folder' AND target_id = ?)",
        (file_id, file_row["folder_id"])
    )

    return {
        "file_id": file_row["file_id"],
        "folder_id": file_row["folder_id"],
        "owner_id": file_row["owner_id"],
        "permissions": perms
    }
