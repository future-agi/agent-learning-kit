def _addresses(*fields):
    """The complete mailbox addresses in address fields ("a@x, Name <b@y>"), lower-cased. Only a
    whole address is one: a display name, or a longer address that contains it, is not."""
    from email.utils import getaddresses

    return {address.strip().lower() for _, address in getaddresses([f for f in fields if f]) if "@" in address}


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
        # A draft quotes the thread it answers, so it is listed only to someone who may read that thread.
        if not _may_read_thread(db, d["thread_id"]):
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


def _may_read_thread(db, thread_id):
    """Whether the person signed in to the conversation (session_caller) may see this mailbox thread.
    The billing mailbox holds every tenant's mail, so the same boundary as the billing tools applies:
    each thread is filed under the tenant it concerns (gmail_thread_tenants). A tenant's billing
    admin sees that tenant's threads; a member sees only threads they wrote or were sent; Cloudnest
    staff in a billing role (billing lead, support, operations, finance) see every thread, an account
    manager only threads of the tenants assigned to them; nobody signed in sees nothing. A thread
    restricted by the mailbox ACL stays closed to everyone, as before."""
    row = db.one("SELECT actor_id FROM session_caller WHERE id = 'default'")
    caller = row["actor_id"] if row else None
    if not caller:
        return False
    filed = db.one("SELECT tenant_id FROM gmail_thread_tenants WHERE thread_id = ?", [thread_id])
    tenant = filed["tenant_id"] if filed else None
    user = db.one("SELECT * FROM users WHERE actor_id = ?", [caller])
    if user is not None:
        if tenant != user["tenant_id"]:
            return False
        if user["role"] == "billing_admin":
            return True
        email = user["email"].lower()
        rows = db.query("SELECT sender, recipient, cc FROM gmail_messages WHERE thread_id = ?", [thread_id])
        rows += db.query("SELECT '' AS sender, recipient, cc FROM gmail_drafts WHERE thread_id = ?", [thread_id])
        return any(email in _addresses(m["sender"], m["recipient"], m["cc"]) for m in rows)
    staff = db.one("SELECT role FROM slack_users WHERE user_id = ?", [caller])
    if staff is None:
        return False
    if staff["role"] in ("billing_lead", "billing_support", "billing_operations", "finance"):
        return True
    return tenant is not None and db.one(
        "SELECT 1 FROM account_assignments WHERE tenant_id = ? AND user_id = ?", [tenant, caller]
    ) is not None
