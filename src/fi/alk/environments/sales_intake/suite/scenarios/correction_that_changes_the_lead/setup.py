def setup(world):
    # Oliver asks for one lead, is corrected about Fernhill, names Harbourline, a colleague adds noise
    # and Dana approves; a separate thread from him asks about another lead.
    for m in [
        {"message_id": "SM412", "channel_id": "CH_SALESHANDOFF", "user_id": "U_A03", "text": "Can I get the handoff summary for Fernhill (L08)?", "ts": "1794300000.000412", "thread_ts": None, "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300000000412"},
        {"message_id": "SM415", "channel_id": "CH_SALESHANDOFF", "user_id": "U_MGR", "text": "Fernhill's summary already went out in your earlier thread on Tuesday. Do you need it again?", "ts": "1794300060.000415", "thread_ts": "1794300000.000412", "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300060000415"},
        {"message_id": "SM413", "channel_id": "CH_SALESHANDOFF", "user_id": "U_A03", "text": "Sorry, I meant Harbourline Insurance (L25), not Fernhill.", "ts": "1794300120.000413", "thread_ts": "1794300000.000412", "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300120000413"},
        {"message_id": "SM416", "channel_id": "CH_SALESHANDOFF", "user_id": "U_A02", "text": "FYI George at Harbourline emailed about invoices last week, in case that's what this is about.", "ts": "1794300180.000416", "thread_ts": "1794300000.000412", "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300180000416"},
        {"message_id": "SM417", "channel_id": "CH_SALESHANDOFF", "user_id": "U_A03", "text": "Not the invoices. It's the Harbourline (L25) handoff summary I need, for Thursday's call. Dana, can you approve it?", "ts": "1794300240.000417", "thread_ts": "1794300000.000412", "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300240000417"},
        {"message_id": "SM418", "channel_id": "CH_SALESHANDOFF", "user_id": "U_MGR", "text": "Approved: the desk can post the Harbourline summary in this thread.", "ts": "1794300300.000418", "thread_ts": "1794300000.000412", "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300300000418"},
        {"message_id": "SM419", "channel_id": "CH_SALESHANDOFF", "user_id": "U_A03", "text": "Also, when someone has a moment: the handoff summary for Kestrel Mobility (L07)?", "ts": "1794300600.000419", "thread_ts": None, "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300600000419"},
    ]:
        world.put("slack_messages", m)
    world.put("slack_approvals", {
        "approval_id": "APP-SH-412",
        "actor_id": "U_SALESDESK",
        "channel_id": "CH_SALESHANDOFF",
        "content_hash": "hash_l25_summary",
        "approved_text": "Harbourline Insurance: 55 seats, customer service, 3-6 months, budget confirmed.",
        "status": "approved",
        "expires_at": "2026-11-20T00:00:00Z",
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S402", "name": "Rachel Holloway", "email": "rachel.holloway@brightdesk.example", "role": "sales_ops"})
    world.change("session_caller", "default", {"person_id": "U_S402"}, by="id")
