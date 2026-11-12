def _caller_collections(db):
    """The collections the employee signed in to this conversation may read. The desk opens Drive on
    their behalf, so a file outside their role's collections stays closed even where the desk's
    own account could open it."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [row["employee_id"]]) if row and row["employee_id"] else None
    if e is None or e["status"] != "active":
        raise ToolError("No active employee is signed in to this conversation; the desk reads Drive only for the signed-in employee.", category="permission_denied")
    return {r["collection_id"] for r in db.query("SELECT collection_id FROM collection_access WHERE role = ?", [e["role"]])}


def _caller_may_read(db, f, readable):
    """Whether a Drive file is in a collection the caller may read: the corpus document it mirrors
    names its collection; any other file goes by its folder."""
    k = db.one("SELECT collection_id FROM kb_documents WHERE drive_file_id = ?", [f["file_id"]])
    c = k or db.one("SELECT collection_id FROM collections WHERE drive_folder_id = ?", [f["folder_id"]])
    return c is None or c["collection_id"] in readable


def handle(args, db):
    if not args.get("fileId"):
        raise ToolError("Missing required argument: fileId", category="invalid_input")
    file_id = str(args["fileId"])
    revision_id = args.get("revisionId")

    file_row = db.one("SELECT * FROM drive_files WHERE file_id = ?", (file_id,))
    if not file_row or file_row["is_deleted"]:
        raise ToolError(f"File '{file_id}' not found or has been deleted.", category="not_found")

    actor_row = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    user_id = actor_row["user_id"] if actor_row else "u_alice"
    user_record = db.one("SELECT role FROM drive_users WHERE user_id = ?", (user_id,))
    is_admin = user_record and user_record["role"] == "administrator"

    if not is_admin:
        perm = db.one("SELECT role FROM drive_permissions WHERE target_type = 'folder' AND target_id = ? AND user_id = ?", (file_row["folder_id"], user_id))
        file_perm = db.one("SELECT role FROM drive_permissions WHERE target_type = 'file' AND target_id = ? AND user_id = ?", (file_id, user_id))
        if not perm and not file_perm:
            raise ToolError(f"Permission denied: You do not have access to file '{file_id}'.", category="permission_denied")
    if not _caller_may_read(db, file_row, _caller_collections(db)):
        raise ToolError(f"Permission denied: file '{file_id}' is outside the collections the signed-in employee may read.", category="permission_denied")

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
