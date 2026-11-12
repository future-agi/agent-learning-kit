def handle(args, db):
    if not args.get("fileId"):
        raise ToolError("Missing required argument: fileId", category="invalid_input")
    file_id = str(args["fileId"])
    revision_id = args.get("revisionId")

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

    content = file_row["content"]
    if revision_id:
        rev = db.one("SELECT * FROM drive_file_revisions WHERE revision_id = ? AND file_id = ?", (str(revision_id), file_id))
        if not rev:
            raise ToolError(f"Revision '{revision_id}' not found for file '{file_id}'.", category="not_found")
        content = rev["content"]

    # An unsupported format is answered, not refused: empty text and textFormattingNotSupported
    # (the official server's behaviour), so the agent has to say it could not read the file.
    unsupported = file_row["mime_type"] in ("application/octet-stream", "application/x-binary", "binary") or content is None
    result = {
        "fileContent": "" if unsupported else content,
        "textFormattingNotSupported": unsupported,
    }
    if args.get("includeComments"):
        result["contentAnchoredComments"] = []
        result["unanchoredComments"] = []
        result["commentsNotSupported"] = True
    if revision_id:
        result["revisionId"] = str(revision_id)
    return result


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
