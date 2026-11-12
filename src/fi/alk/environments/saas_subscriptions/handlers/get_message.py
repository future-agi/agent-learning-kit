def handle(args, db):
    message_id = (args.get("messageId") or "").strip() if isinstance(args.get("messageId"), str) else ""
    if not message_id:
        raise ToolError("messageId is required", category="invalid_input")
    message_format = args.get("messageFormat") or "FULL_CONTENT"
    if message_format not in ("MESSAGE_FORMAT_UNSPECIFIED", "MINIMAL", "FULL_CONTENT", "METADATA_ONLY"):
        raise ToolError(f"Unknown messageFormat {message_format!r}", category="invalid_input")

    m = db.one("SELECT * FROM gmail_messages WHERE id = ?", (message_id,))
    if m is None:
        raise ToolError(f"Message {message_id!r} not found", category="not_found")
    t = db.one("SELECT is_restricted FROM gmail_threads WHERE id = ?", (m["thread_id"],))
    if t and t["is_restricted"]:
        raise ToolError(f"Access to message {message_id!r} is restricted by mailbox ACL", category="permission_denied")
    if not _may_read_thread(db, m["thread_id"]):
        raise ToolError(f"Message {message_id!r} is not mail you may see: its thread is filed under another tenant, or no one is signed in", category="permission_denied")

    labels = [row["label_id"] for row in db.query("SELECT label_id FROM gmail_thread_labels WHERE thread_id = ?", (m["thread_id"],))]
    message = {
        "id": m["id"],
        "snippet": m["snippet"],
        "subject": m["subject"],
        "sender": m["sender"],
        "toRecipients": [x.strip() for x in m["recipient"].split(",") if x.strip()],
        "ccRecipients": [x.strip() for x in (m["cc"] or "").split(",") if x.strip()],
        "date": m["date"][:10],
        "attachmentIds": [],
        "attachments": [],
        "labelIds": labels,
    }
    if message_format in ("FULL_CONTENT", "MESSAGE_FORMAT_UNSPECIFIED"):
        message["plaintextBody"] = m["body"]
        message["htmlBody"] = ""
    return message


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
        return any(email in f"{m['sender']},{m['recipient']},{m['cc'] or ''}".lower() for m in rows)
    staff = db.one("SELECT role FROM slack_users WHERE user_id = ?", [caller])
    if staff is None:
        return False
    if staff["role"] in ("billing_lead", "billing_support", "billing_operations", "finance"):
        return True
    return tenant is not None and db.one(
        "SELECT 1 FROM account_assignments WHERE tenant_id = ? AND user_id = ?", [tenant, caller]
    ) is not None
