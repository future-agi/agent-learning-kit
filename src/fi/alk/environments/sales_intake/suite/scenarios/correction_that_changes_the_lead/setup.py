def setup(world):
    for m in [
        {
            "message_id": "SM412",
            "channel_id": "CH_SALESHANDOFF",
            "user_id": "U_A03",
            "text": "Can I get the handoff summary for Fernhill (L08)?",
            "ts": "1794300000.000412",
            "thread_ts": None,
            "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300000000412",
        },
        {
            "message_id": "SM413",
            "channel_id": "CH_SALESHANDOFF",
            "user_id": "U_A03",
            "text": "Sorry, I meant Harbourline Insurance (L25), not Fernhill.",
            "ts": "1794300000.000413",
            "thread_ts": "1794300000.000412",
            "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300000000413",
        },
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
