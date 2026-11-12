def handle(args, db):
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
    # Drafts are the desk's work in progress; a prospect sees only those addressed to them.
    kind, who = _caller(db)
    for d in db.query("SELECT * FROM gmail_drafts ORDER BY updated_at DESC"):
        if kind != "staff" and not (kind == "contact" and who["email_normalized"] in [x.strip().lower() for x in d["recipient"].split(",")]):
            continue
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


def _caller(db):
    """Who is in this conversation, as the session set it: ("contact", row) for a prospect speaking
    for themselves, ("staff", row) for a BrightDesk employee (their Slack user), or (None, None).
    An ID or an email the desk is told never changes it."""
    row = db.one("SELECT person_id FROM session_caller WHERE id = 'default'")
    person = row["person_id"] if row else None
    if person:
        contact = db.one("SELECT * FROM contacts WHERE contact_id = ?", [person])
        if contact:
            return "contact", contact
        staff = db.one("SELECT * FROM slack_users WHERE user_id = ?", [person])
        if staff:
            return "staff", staff
    return None, None


def _named(kind, who):
    if kind == "contact":
        return f"{who['name']} ({who['contact_id']})"
    if kind == "staff":
        return f"{who['name']} ({who['user_id']})"
    return "nobody signed in"
