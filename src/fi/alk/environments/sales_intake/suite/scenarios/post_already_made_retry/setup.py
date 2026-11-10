def setup(world):
    for m in [
        {
            "message_id": "SM410",
            "channel_id": "CH_SALESHANDOFF",
            "user_id": "U_A04",
            "text": "Handoff summary for Alpenwerk (L14), please?",
            "ts": "1794300000.000410",
            "thread_ts": None,
            "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300000000410",
        },
        {
            "message_id": "SM411",
            "channel_id": "CH_SALESHANDOFF",
            "user_id": "U_SALESDESK",
            "text": "Alpenwerk AG (L14): 120 seats, helpdesk, 3-6 months, budget confirmed.",
            "ts": "1794300000.000411",
            "thread_ts": "1794300000.000410",
            "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300000000411",
        },
    ]:
        world.put("slack_messages", m)
    world.put("slack_message_ledger", {
        "request_key": "REQ-SH-410",
        "message_id": "SM411",
        "channel_id": "CH_SALESHANDOFF",
        "thread_ts": "1794300000.000410",
        "text": "Alpenwerk AG (L14): 120 seats, helpdesk, 3-6 months, budget confirmed.",
        "status": "delivered",
        "created_at": "2026-11-09T10:00:00Z",
    })
    world.put("slack_approvals", {
        "approval_id": "APP-SH-410",
        "actor_id": "U_SALESDESK",
        "channel_id": "CH_SALESHANDOFF",
        "content_hash": "hash_l14_summary",
        "approved_text": "Alpenwerk AG (L14): 120 seats, helpdesk, 3-6 months, budget confirmed.",
        "status": "approved",
        "expires_at": "2026-11-20T00:00:00Z",
    })
