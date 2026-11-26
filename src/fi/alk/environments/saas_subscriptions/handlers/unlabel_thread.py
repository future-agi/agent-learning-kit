def _addresses(*fields):
    """The complete mailbox addresses in address fields ("a@x, Name <b@y>"), lower-cased. Only a
    whole address is one: a display name, or a longer address that contains it, is not."""
    from email.utils import getaddresses

    return {address.strip().lower() for _, address in getaddresses([f for f in fields if f]) if "@" in address}


def handle(args, db):
    thread_id = (args.get("threadId") or "").strip() if isinstance(args.get("threadId"), str) else ""
    if not thread_id:
        raise ToolError("threadId is required", category="invalid_input")
    label_ids = args.get("labelIds")
    if not isinstance(label_ids, list) or not label_ids or not all(isinstance(x, str) and x for x in label_ids):
        raise ToolError("labelIds must be a non-empty list of label IDs", category="invalid_input")

    t = db.one("SELECT * FROM gmail_threads WHERE id = ?", (thread_id,))
    if t is None:
        raise ToolError(f"Thread {thread_id!r} not found", category="not_found")
    if t["is_restricted"]:
        raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")
    if not _may_read_thread(db, thread_id):
        raise ToolError(f"Thread {thread_id!r} is not mail you may see: it is filed under another tenant, or no one is signed in", category="permission_denied")

    for lid in label_ids:
        if db.one("SELECT id FROM gmail_labels WHERE id = ?", (lid,)) is None:
            raise ToolError(f"Label {lid!r} does not exist; labelIds takes label IDs, not names", category="not_found")
    for lid in label_ids:
        db.execute("DELETE FROM gmail_thread_labels WHERE thread_id = ? AND label_id = ?", (thread_id, lid))
    return {}


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
