def handle(args, db):
    if not args.get("fileId"):
        raise ToolError("Missing required argument: fileId", category="invalid_input")
    file_id = str(args["fileId"])

    file_row = db.one("SELECT * FROM files WHERE file_id = ?", (file_id,))
    if not file_row or file_row["is_deleted"]:
        raise ToolError(f"File '{file_id}' not found or has been deleted.", category="not_found")

    actor_row = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    user_id = actor_row["user_id"] if actor_row else "u_alice"
    user_record = db.one("SELECT role FROM users WHERE user_id = ?", (user_id,))
    is_admin = user_record and user_record["role"] == "administrator"

    if not is_admin:
        perm = db.one("SELECT role FROM permissions WHERE target_type = 'folder' AND target_id = ? AND user_id = ?", (file_row["folder_id"], user_id))
        file_perm = db.one("SELECT role FROM permissions WHERE target_type = 'file' AND target_id = ? AND user_id = ?", (file_id, user_id))
        if not perm and not file_perm:
            raise ToolError(f"Permission denied: You do not have access to file '{file_id}'.", category="permission_denied")

    # The file's own grants plus the folder's it inherits, in the official Permission shape.
    perms = db.query(
        "SELECT p.role, p.user_id, u.email, u.name FROM permissions p LEFT JOIN users u ON u.user_id = p.user_id "
        "WHERE (p.target_type = 'file' AND p.target_id = ?) OR (p.target_type = 'folder' AND p.target_id = ?) ORDER BY p.permission_id",
        (file_id, file_row["folder_id"]),
    )
    roles = {"owner": "owner", "editor": "writer", "viewer": "reader", "commenter": "commenter"}
    return {
        "permissions": [
            {
                "role": roles.get(p["role"], p["role"]),
                "type": "user",
                "emailAddress": p["email"] or "",
                "displayName": p["name"] or p["user_id"],
            }
            for p in perms
        ]
    }
