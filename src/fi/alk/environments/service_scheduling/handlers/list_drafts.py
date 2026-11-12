def _caller(db):
    """Who is in this conversation, as the session knows it (session_caller), never as the
    conversation tells it: a customer, a front-desk member, or nobody identified."""
    row = db.one("SELECT customer_id, desk_id FROM session_caller WHERE id = 'default'")
    return (row["customer_id"], row["desk_id"]) if row else (None, None)


def _desk(db):
    """Whether the caller is an active front-desk member, who may act for any customer."""
    _, desk_id = _caller(db)
    return bool(desk_id and db.one("SELECT 1 AS x FROM desk_staff WHERE desk_id = ? AND role = 'front_desk' AND active = 1", [desk_id]))


def _desk_only(db, what):
    """Drafts and labels are the front desk's own work on the mailbox."""
    if not _desk(db):
        raise ToolError(f"Permission denied: only the front desk can {what}", category="permission_denied")

def handle(args, db):
    _desk_only(db, "read the mailbox's drafts")
    page_size = args.get("pageSize")
    if page_size is None:
        page_size = 20
    if not isinstance(page_size, int) or page_size < 1 or page_size > 50:
        raise ToolError("pageSize must be an integer from 1 to 50", category="invalid_input")
    offset = 0
    if args.get("pageToken"):
        try:
            offset = int(args["pageToken"])
        except ValueError:
            raise ToolError(f"Invalid pageToken: {args['pageToken']!r}", category="invalid_input")
    view = args.get("view") or "DRAFT_VIEW_FULL"
    if view not in ("DRAFT_VIEW_UNSPECIFIED", "DRAFT_VIEW_METADATA_ONLY", "DRAFT_VIEW_FULL"):
        raise ToolError(f"Unknown view {view!r}", category="invalid_input")
    terms, filters = [], []
    for part in (args.get("query") or "").split():
        operator, colon, value = part.partition(":")
        if not colon:
            terms.append(part.lower())
        elif operator.lower() in ("to", "cc", "bcc", "subject") and value:
            filters.append((operator.lower(), value.strip('"').lower()))
        else:
            raise ToolError(f"Unsupported draft search operator {part!r}; drafts support to:, cc:, bcc: and subject:", category="invalid_input")

    drafts = []
    for d in db.query("SELECT * FROM gmail_drafts ORDER BY updated_at DESC"):
        text = f"{d['subject']} {d['body']} {d['recipient']}".lower()
        if terms and not all(term in text for term in terms):
            continue
        fields = {"to": d["recipient"], "cc": d["cc"] or "", "bcc": d["bcc"] or "", "subject": d["subject"]}
        if any(value not in fields[operator].lower() for operator, value in filters):
            continue
        draft = {
            "id": d["id"],
            "threadId": d["thread_id"],
            "toRecipients": [x.strip() for x in d["recipient"].split(",") if x.strip()],
            "ccRecipients": [x.strip() for x in (d["cc"] or "").split(",") if x.strip()],
            "bccRecipients": [x.strip() for x in (d["bcc"] or "").split(",") if x.strip()],
            "date": d["created_at"][:10],
            "humanEdited": bool(d["is_human_edited"]),
        }
        if view != "DRAFT_VIEW_METADATA_ONLY":
            draft["subject"] = d["subject"]
            draft["plaintextBody"] = d["body"]
            draft["htmlBody"] = ""
        drafts.append(draft)

    paged = drafts[offset : offset + page_size]
    result = {"drafts": paged}
    if offset + page_size < len(drafts):
        result["nextPageToken"] = str(offset + page_size)
    return result
