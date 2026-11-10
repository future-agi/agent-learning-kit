def setup(world):
    world.put("message_ledger", {
        "request_key": "REQ-TIMEOUT-501",
        "message_id": "MSG-NEW-501",
        "channel_id": "C01",
        "thread_ts": None,
        "text": "Project digest: All backend migrations succeeded.",
        "status": "sent",
        "created_at": "2026-10-26T09:15:00Z"
    })
    world.put("messages", {
        "message_id": "MSG-NEW-501",
        "channel_id": "C01",
        "user_id": "U01",
        "text": "Project digest: All backend migrations succeeded.",
        "ts": "1729934100.000100",
        "thread_ts": None,
        "permalink": "https://slack.example.com/archives/C01/p1729934100000100"
    })
