def setup(world):
    world.put("slack_messages", {
        "message_id": "SM405",
        "channel_id": "CH_BILLESC",
        "user_id": "U_AM2",
        "text": "GrowthLabs approved the move to Enterprise, please schedule it",
        "ts": "1792000000.000405",
        "thread_ts": None,
        "permalink": "https://cloudnest.slack.example.com/archives/CH_BILLESC/p1792000000000405"
    })
    world.put("slack_users", {"user_id": "U_BL1", "name": "Toby Wright", "email": "toby.wright@cloudnest.example", "role": "billing_lead"})
    world.put("slack_thread_tenants", {"channel_id": "CH_BILLESC", "thread_ts": "1792000000.000405", "tenant_id": "T07"})
    world.change("session_caller", "default", {"actor_id": "U_BL1"}, by="id")
