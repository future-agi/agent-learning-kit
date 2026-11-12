def setup(world):
    world.put("slack_messages", {
        "channel_id": "CH_BILLESC",
        "message_id": "SM402",
        "permalink": "https://cloudnest.slack.example.com/archives/CH_BILLESC/p1792000000000402",
        "text": "HyperScale (T08) wants to add 20 seats to their Enterprise plan, taking them to 140 seats. What is the preview delta?",
        "thread_ts": None,
        "ts": "1792000000.000402",
        "user_id": "U_AM2"
    })
    world.put("account_assignments", {"tenant_id": "T08", "user_id": "U_AM2"})
    world.change("session_caller", "default", {"actor_id": "U_AM2"}, by="id")
