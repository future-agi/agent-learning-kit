def setup(world):
    world.put("approvals", {
        "approval_id": "APP-201",
        "actor_id": "U03",
        "channel_id": "C01",
        "content_hash": "hash_proj_digest",
        "approved_text": "Weekly project digest for proj-launch",
        "status": "approved",
        "expires_at": "2026-10-27T12:00:00Z"
    })
    world.put("messages", {
        "message_id": "M101",
        "channel_id": "C01",
        "user_id": "U03",
        "text": "Decision confirmed: We will use PostgreSQL for the v2 architecture.",
        "ts": "1729850010.000300",
        "thread_ts": "1729850000.000100",
        "permalink": "https://slack.example.com/archives/C01/p1729850010000300"
    })
    world.put("messages", {
        "message_id": "M102",
        "channel_id": "C01",
        "user_id": "U05",
        "text": "BLOCKER: Staging network timeout between gateway and auth service is failing integration tests.",
        "ts": "1729853000.000100",
        "thread_ts": "1729853000.000100",
        "permalink": "https://slack.example.com/archives/C01/p1729853000000100"
    })
