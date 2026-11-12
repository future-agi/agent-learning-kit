def handle(args, db):
    order_by = args.get("orderBy") or "recency"
    if order_by not in ("recency", "lastModified", "lastModifiedByMe"):
        order_by = "recency"
    if args.get("pageSize") is not None and (not isinstance(args["pageSize"], int) or args["pageSize"] < 1):
        raise ToolError("pageSize must be a positive integer", category="invalid_input")
    page_size = args.get("pageSize") or 10
    page_token = args.get("pageToken")
    offset = int(page_token) if page_token and str(page_token).isdigit() else 0
    with_snippets = not args.get("excludeContentSnippets")

    _drive_reader(db)
    actor_row = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    user_id = actor_row["user_id"] if actor_row else "u_alice"
    user_record = db.one("SELECT role FROM drive_users WHERE user_id = ?", (user_id,))
    is_admin = user_record and user_record["role"] == "administrator"
    owners = {u["user_id"]: u["email"] for u in db.query("SELECT user_id, email FROM drive_users")}

    files = db.query("SELECT * FROM drive_files WHERE NOT is_deleted ORDER BY modified_at DESC, file_id ASC")
    if order_by == "lastModifiedByMe":
        files = [f for f in files if f["owner_id"] == user_id]

    accessible = []
    for f in files:
        if _restricted(db, f["folder_id"]):
            continue
        if not is_admin:
            perm = db.one("SELECT role FROM drive_permissions WHERE target_type = 'folder' AND target_id = ? AND user_id = ?", (f["folder_id"], user_id))
            file_perm = db.one("SELECT role FROM drive_permissions WHERE target_type = 'file' AND target_id = ? AND user_id = ?", (f["file_id"], user_id))
            if not perm and not file_perm:
                continue
        content = f["content"] or ""
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
            "owner": owners.get(f["owner_id"], ""),
            "canAddChildren": False,
        }
        if with_snippets:
            item["contentSnippet"] = content[:200]
        accessible.append(item)

    paged = accessible[offset : offset + page_size]
    result = {"files": paged}
    if offset + len(paged) < len(accessible):
        result["nextPageToken"] = str(offset + len(paged))
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
