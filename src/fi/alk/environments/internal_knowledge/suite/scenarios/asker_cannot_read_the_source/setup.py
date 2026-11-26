def setup(world):
    world.change("session_caller", "default", {"employee_id": "E03"}, by="id")
    world.put("slack_messages", {
        "message_id": "KM415",
        "channel_id": "CH_HELPPOL",
        "user_id": "U_E20",
        "text": "Do we need a purchase order for a $12,000 laptop order?",
        "ts": "1794100000.000415",
        "thread_ts": None,
        "permalink": "https://acme.slack.example.com/archives/CH_HELPPOL/p1794100000000415"
    })
    world.put("slack_approvals", {
        "approval_id": "APP-KN-426",
        "actor_id": "U_KDESK",
        "channel_id": "CH_HELPPOL",
        "content_hash": "",
        "approved_text": "Purchase order policies are managed in the Finance & Procurement collection. Please check with your manager or the Finance team for guidance on purchase orders.",
        "status": "approved",
        "expires_at": "2026-11-30T00:00:00Z"
    })