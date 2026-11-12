def handle(args, db):
    if not args.get("fileId"):
        raise ToolError("Missing required argument: fileId", category="invalid_input")
    file_id = str(args["fileId"])

    file_row = db.one("SELECT * FROM drive_files WHERE file_id = ?", (file_id,))
    if not file_row or file_row["is_deleted"]:
        raise ToolError(f"File '{file_id}' not found or has been deleted.", category="not_found")

    _drive_reader(db)
    if _restricted(db, file_row["folder_id"]):
        raise ToolError(f"Permission denied: '{file_id}' is in a restricted folder the desk does not read for anyone.", category="permission_denied")
    actor_row = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    user_id = actor_row["user_id"] if actor_row else "u_alice"
    user_record = db.one("SELECT role FROM drive_users WHERE user_id = ?", (user_id,))
    is_admin = user_record and user_record["role"] == "administrator"

    if not is_admin:
        perm = db.one("SELECT role FROM drive_permissions WHERE target_type = 'folder' AND target_id = ? AND user_id = ?", (file_row["folder_id"], user_id))
        file_perm = db.one("SELECT role FROM drive_permissions WHERE target_type = 'file' AND target_id = ? AND user_id = ?", (file_id, user_id))
        if not perm and not file_perm:
            raise ToolError(f"Permission denied: You do not have access to file '{file_id}'.", category="permission_denied")

    # The file's own grants plus the folder's it inherits, in the official Permission shape.
    perms = db.query(
        "SELECT p.role, p.user_id, u.email, u.name FROM drive_permissions p LEFT JOIN drive_users u ON u.user_id = p.user_id "
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


def _drive_reader(db):
    """The person signed in to the conversation, who must be an active employee for the desk to
    read the policy library for them. The library's internal folders are every employee's to read;
    a restricted folder is read only by its own owners, never through the desk (whose Drive grants
    do not reach it), whoever is signed in."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    caller = row["employee_id"] if row else None
    emp = db.one("SELECT status FROM employees WHERE employee_id = ?", [caller]) if caller else None
    if not emp or emp["status"] != "active":
        raise ToolError(f"Permission denied: nobody active is signed in to this conversation ({caller or 'nobody'}), so the desk reads no documents.", category="permission_denied")
    return caller


def _restricted(db, folder_id):
    folder = db.one("SELECT access_level FROM drive_folders WHERE folder_id = ?", [folder_id])
    return not folder or folder["access_level"] != "internal"
