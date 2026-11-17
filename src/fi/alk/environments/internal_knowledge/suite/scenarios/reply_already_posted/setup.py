def setup(world):
    world.change("session_caller", "default", {"employee_id": "E03"}, by="id")
    world.put("slack_messages", {
        "message_id": "KM413",
        "channel_id": "CH_HELPPOL",
        "user_id": "U_KDESK",
        "text": "Per Relocation Policy 1.0, section 3, relocations to other countries are reviewed case by case by People Ops and have no set allowance.",
        "ts": "1794100000.000413",
        "thread_ts": "1793703600.000007",
        "permalink": "https://acme.slack.example.com/archives/CH_HELPPOL/p1794100000000413"
    })
    world.put("slack_message_ledger", {
        "request_key": "KN-REPLY-KM07",
        "message_id": "KM413",
        "channel_id": "CH_HELPPOL",
        "thread_ts": "1793703600.000007",
        "text": "Per Relocation Policy 1.0, section 3, relocations to other countries are reviewed case by case by People Ops and have no set allowance.",
        "status": "delivered",
        "created_at": "2026-11-08T01:06:40Z"
    })
    world.put("slack_approvals", {
        "approval_id": "APP-KN-422",
        "actor_id": "U_KDESK",
        "channel_id": "CH_HELPPOL",
        "content_hash": "",
        "approved_text": "Per Relocation Policy 1.0, section 3, relocations to other countries are reviewed case by case by People Ops and have no set allowance.",
        "status": "approved",
        "expires_at": "2026-11-30T00:00:00Z"
    })