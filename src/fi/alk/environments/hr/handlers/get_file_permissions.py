def _signed_in(db):
    """The person in this conversation. The HR policy library is the company's, open to every
    employee signed in; nobody signed in reads nothing."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    if not row or not row["employee_id"]:
        raise ToolError("Permission denied: nobody is signed in to this conversation.", category="permission_denied")
    return row["employee_id"]


def _restricted(db, folder_id):
    """Whether a file's folder is restricted (compensation). No employee role in this world is
    entitled to it through the desk, HR included, so it is refused whoever is signed in, whatever
    the desk's own grants say."""
    row = db.one("SELECT access_level FROM drive_folders WHERE folder_id = ?", (folder_id,))
    return bool(row) and row["access_level"] == "restricted"


def handle(args, db):
    if not args.get("fileId"):
        raise ToolError("Missing required argument: fileId", category="invalid_input")
    file_id = str(args["fileId"])

    file_row = db.one("SELECT * FROM drive_files WHERE file_id = ?", (file_id,))
    if not file_row or file_row["is_deleted"]:
        raise ToolError(f"File '{file_id}' not found or has been deleted.", category="not_found")
    _signed_in(db)
    if _restricted(db, file_row["folder_id"]):
        raise ToolError(f"Permission denied: You do not have access to file '{file_id}'.", category="permission_denied")

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
