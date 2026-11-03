def handle(args, db):
    if not args.get("fileId"):
        raise ToolError("Missing required argument: fileId", category="invalid_input")
    file_id = str(args["fileId"])
    revision_id = args.get("revisionId")

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

    content = file_row["content"]
    if revision_id:
        rev = db.one("SELECT * FROM file_revisions WHERE revision_id = ? AND file_id = ?", (str(revision_id), file_id))
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
