def handle(args, db):
    if "file_id" not in args or not args["file_id"]:
        raise ToolError("Missing required argument: file_id")
    file_id = str(args["file_id"])
    revision_id = args.get("revision_id")

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

    if file_row["mime_type"] in ("application/octet-stream", "application/x-binary", "binary") or (file_row["content"] is None and revision_id is None):
        raise ToolError(f"Unsupported file format '{file_row['mime_type']}': Cannot read binary or non-text document.")

    content = file_row["content"]
    rev_result = None

    if revision_id:
        rev = db.one("SELECT * FROM file_revisions WHERE revision_id = ? AND file_id = ?", (str(revision_id), file_id))
        if not rev:
            raise ToolError(f"Revision '{revision_id}' not found for file '{file_id}'.")
        content = rev["content"]
        rev_result = rev["revision_id"]

    return {
        "content": content,
        "effective_date": file_row["effective_date"],
        "file_id": file_row["file_id"],
        "mime_type": file_row["mime_type"],
        "revision_id": rev_result,
        "status": file_row["status"],
        "title": file_row["title"],
        "version": file_row["version"]
    }
