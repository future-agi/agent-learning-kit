def handle(args, db):
    from datetime import datetime, timedelta

    page_size = args.get("pageSize")
    if page_size is None:
        page_size = 20
    if not isinstance(page_size, int) or page_size < 1 or page_size > 50:
        raise ToolError("pageSize must be an integer from 1 to 50", category="invalid_input")
    offset = 0
    page_token = args.get("pageToken")
    if page_token:
        try:
            offset = int(page_token)
        except ValueError:
            raise ToolError(f"Invalid pageToken: {page_token!r}", category="invalid_input")
    include_trash = bool(args.get("includeTrash"))

    clock_row = db.one("SELECT now FROM clock")
    now = datetime.fromisoformat((clock_row["now"] if clock_row else "2026-10-26T10:00:00Z").replace("Z", "+00:00"))

    # Gmail search syntax, the operators this mailbox answers; a bare term matches subject, body
    # or sender. Drafts are never listed here.
    label_ids, terms, sender, recipient, subject, newer_than, older_than = [], [], None, None, None, None, None
    for part in (args.get("query") or "").split():
        lower = part.lower()
        if lower.startswith("label:"):
            label_ids.append(part.split(":", 1)[1].upper())
        elif lower == "is:unread":
            label_ids.append("UNREAD")
        elif lower in ("in:inbox", "is:inbox"):
            label_ids.append("INBOX")
        elif lower in ("in:anywhere", "-in:draft", "in:draft"):
            continue
        elif lower.startswith("from:"):
            sender = part.split(":", 1)[1].lower()
        elif lower.startswith("to:"):
            recipient = part.split(":", 1)[1].lower()
        elif lower.startswith("subject:"):
            subject = part.split(":", 1)[1].lower()
        elif lower.startswith("newer_than:") or lower.startswith("older_than:"):
            amount = part.split(":", 1)[1]
            units = {"d": 1, "w": 7, "m": 30, "y": 365}
            if not amount[:-1].isdigit() or amount[-1] not in units:
                raise ToolError(f"{part}: use a number followed by d, w, m or y", category="invalid_input")
            delta = timedelta(days=int(amount[:-1]) * units[amount[-1]])
            if lower.startswith("newer_than:"):
                newer_than = now - delta
            else:
                older_than = now - delta
        elif lower == "has:attachment":
            return {"threads": [], "resultCountEstimate": "0"}
        else:
            terms.append(lower)

    threads = db.query("SELECT * FROM gmail_threads WHERE is_restricted = FALSE ORDER BY updated_at DESC")
    matched = []
    for t in threads:
        t_labels = [row["label_id"] for row in db.query("SELECT label_id FROM gmail_thread_labels WHERE thread_id = ?", (t["id"],))]
        if "SPAM" in t_labels or ("TRASH" in t_labels and not include_trash):
            continue
        # Search shows only what a direct read would: no subject, snippet or sender of mail the
        # caller may not open.
        if not _may_read_thread(db, t["id"]):
            continue
        if any(lid not in t_labels for lid in label_ids):
            continue
        msgs = db.query("SELECT * FROM gmail_messages WHERE thread_id = ? ORDER BY date ASC", (t["id"],))
        if sender and not any(sender in m["sender"].lower() for m in msgs):
            continue
        if recipient and not any(recipient in m["recipient"].lower() for m in msgs):
            continue
        if subject and subject not in t["subject"].lower():
            continue
        if newer_than or older_than:
            latest = datetime.fromisoformat(max(m["date"] for m in msgs).replace("Z", "+00:00")) if msgs else now
            if newer_than and latest < newer_than:
                continue
            if older_than and latest > older_than:
                continue
        if terms:
            text = (t["subject"] + " " + t["snippet"] + " " + " ".join(m["subject"] + " " + m["body"] + " " + m["sender"] for m in msgs)).lower()
            if not all(term in text for term in terms):
                continue
        matched.append({
            "id": t["id"],
            "messages": [
                {
                    "id": m["id"],
                    "snippet": m["snippet"],
                    "subject": m["subject"],
                    "sender": m["sender"],
                    "toRecipients": [x.strip() for x in m["recipient"].split(",") if x.strip()],
                    "ccRecipients": [x.strip() for x in (m["cc"] or "").split(",") if x.strip()],
                    "date": m["date"][:10],
                    "labelIds": t_labels,
                }
                for m in msgs
            ],
        })

    paged = matched[offset : offset + page_size]
    result = {"threads": paged, "resultCountEstimate": str(len(matched))}
    if offset + page_size < len(matched):
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
        return any(email in f"{m['sender']},{m['recipient']},{m['cc'] or ''}".lower() for m in rows)
    staff = db.one("SELECT role FROM slack_users WHERE user_id = ?", [caller])
    if staff is None:
        return False
    if staff["role"] in ("billing_lead", "billing_support", "billing_operations", "finance"):
        return True
    return tenant is not None and db.one(
        "SELECT 1 FROM account_assignments WHERE tenant_id = ? AND user_id = ?", [tenant, caller]
    ) is not None
