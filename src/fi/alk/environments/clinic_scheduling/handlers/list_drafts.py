def _withheld(db, thread_id):
    """The patient a mailbox thread is about whom this conversation may not see yet, or None. The
    appointments mailbox holds patients' health details: a thread linked to a patient is shown only
    once that patient is verified by the person talking, who must be them or hold recorded
    guardian or proxy authority for them."""
    row = db.one("SELECT caller_id FROM session_caller WHERE id = 'default'")
    caller = row["caller_id"] if row else None
    for link in db.query("SELECT patient_id FROM gmail_thread_patients WHERE thread_id = ?", (thread_id,)):
        pid = link["patient_id"]
        entitled = caller is not None and (caller == pid or db.one(
            "SELECT patient_id FROM caller_access WHERE caller_id = ? AND patient_id = ?", (caller, pid)) is not None)
        if not entitled or not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", (pid,)):
            return pid
    return None


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
    for d in db.query("SELECT * FROM gmail_drafts ORDER BY updated_at DESC"):
        if _withheld(db, d["thread_id"]):
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
