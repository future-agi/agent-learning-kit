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
    import re

    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolError("Missing required argument: query", category="invalid_input")
    if args.get("pageSize") is not None and (not isinstance(args["pageSize"], int) or args["pageSize"] < 1):
        raise ToolError("pageSize must be a positive integer", category="invalid_input")
    page_size = args.get("pageSize") or 10
    page_token = args.get("pageToken")
    offset = int(page_token) if page_token and str(page_token).isdigit() else 0
    with_snippets = not args.get("excludeContentSnippets")

    # Drive query syntax: `term operator 'value'` clauses joined by `and` (official grammar; the
    # subset below is what this world answers). `or`, `not` and parentheses are refused.
    clause = re.compile(r"^(title|fullText|mimeType|parentId|owner)\s*(contains|=|!=)\s*'((?:[^'\\]|\\')*)'$")
    clauses = []
    for part in re.split(r"\s+and\s+", query.strip()):
        m = clause.match(part.strip())
        if not m:
            raise ToolError(
                f"Unsupported query clause {part.strip()!r}: use `term operator 'value'` with term in "
                "title, fullText, mimeType, parentId, owner and clauses joined by `and`",
                category="invalid_input",
            )
        term, op, value = m.groups()
        if (term == "fullText" and op != "contains") or (term in ("parentId", "owner") and op == "contains"):
            raise ToolError(f"{term} does not support {op}", category="invalid_input")
        clauses.append((term, op, value.replace("\\'", "'")))

    actor_row = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    user_id = actor_row["user_id"] if actor_row else "u_alice"
    user_record = db.one("SELECT role FROM drive_users WHERE user_id = ?", (user_id,))
    is_admin = user_record and user_record["role"] == "administrator"
    owners = {u["user_id"]: u["email"] for u in db.query("SELECT user_id, email FROM drive_users")}

    def matches(f):
        for term, op, value in clauses:
            if term == "title":
                field, needle = f["title"].lower(), value.lower()
            elif term == "fullText":
                field = f"{f['title']} {f['content'] or ''} {f['policy_family'] or ''}".lower()
                needle = value.lower()
            elif term == "mimeType":
                field, needle = f["mime_type"], value
            elif term == "parentId":
                field, needle = f["folder_id"], value
            else:
                field = owners.get(f["owner_id"], "")
                needle = owners.get(user_id, "") if value == "me" else value
            held = needle in field if op == "contains" else (field == needle) == (op == "=")
            if not held:
                return False
        return True

    files = db.query("SELECT * FROM drive_files WHERE NOT is_deleted ORDER BY file_id")
    readable = _caller_collections(db)
    accessible = []
    for f in files:
        if not is_admin:
            perm = db.one("SELECT role FROM drive_permissions WHERE target_type = 'folder' AND target_id = ? AND user_id = ?", (f["folder_id"], user_id))
            file_perm = db.one("SELECT role FROM drive_permissions WHERE target_type = 'file' AND target_id = ? AND user_id = ?", (f["file_id"], user_id))
            if not perm and not file_perm:
                continue
        if not _caller_may_read(db, f, readable):
            continue
        if not matches(f):
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
