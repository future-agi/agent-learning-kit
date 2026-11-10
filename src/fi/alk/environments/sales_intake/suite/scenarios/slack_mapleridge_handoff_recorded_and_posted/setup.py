def setup(world):
    world.put("slack_messages", {
        "message_id": "SM401",
        "channel_id": "CH_SALESHANDOFF",
        "user_id": "U_A02",
        "text": "Can I get the handoff summary for Maple Ridge Telecom (L03)?",
        "ts": "1794300000.000401",
        "thread_ts": None,
        "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESHANDOFF/p1794300000000401"
    })
    world.put("slack_approvals", {
        "approval_id": "APP-SH-406",
        "actor_id": "U_SALESDESK",
        "channel_id": "CH_SALESHANDOFF",
        "content_hash": "dana-app-406-mapleridge",
        "approved_text": "Maple Ridge Telecom (L03) handoff summary",
        "status": "approved",
        "expires_at": "2026-11-15T15:00:00Z"
    })