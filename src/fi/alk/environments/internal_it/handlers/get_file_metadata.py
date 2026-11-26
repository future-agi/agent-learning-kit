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

    owner = db.one("SELECT email FROM drive_users WHERE user_id = ?", (file_row["owner_id"],))
    content = file_row["content"] or ""
    f = file_row
    item = {
        "id": f["file_id"],
        "title": f["title"],
        "parentId": f["folder_id"],
        "mimeType": f["mime_type"],
        "fileSize": str(len(content.encode("utf-8"))),
        "description": f"{f['policy_family'] or 'document'}; status {f['status']}; version {f['version']}; effective {f['effective_date'] or 'n/a'}",
        "fileExtension": f["title"].rsplit(".", 1)[-1] if "." in f["title"] else "",
        "viewUrl": f"https://drive.example.com/file/d/{f['file_id']}/view",
        "createdTime": f["created_at"],
        "modifiedTime": f["modified_at"],
        "owner": owner["email"] if owner else "",
        "canAddChildren": False,
    }
    if not args.get("excludeContentSnippets"):
        item["contentSnippet"] = content[:200]
    return item


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
