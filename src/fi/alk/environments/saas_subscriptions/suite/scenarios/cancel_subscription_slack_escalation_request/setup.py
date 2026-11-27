def setup(world):
    world.put("slack_messages", {
        "message_id": "SM401",
        "channel_id": "CH_BILLESC",
        "user_id": "U_AM1",
        "text": "KiteWorks (T11) admin Karen Kite confirmed they want to cancel at the end of their current term.",
        "ts": "1792000000.000401",
        "thread_ts": None,
        "permalink": "https://cloudnest.slack.example.com/archives/CH_BILLESC/p1792000000000401"
    })
    world.put("slack_thread_tenants", {"channel_id": "CH_BILLESC", "thread_ts": "1792000000.000401", "tenant_id": "T11"})
    world.change("session_caller", "default", {"actor_id": "U11_ADMIN"}, by="id")
