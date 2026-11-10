def setup(world):
    world.put("slack_users", {
        "user_id": "U_A414",
        "name": "Oliver Kingsley",
        "email": "oliver.kingsley@brightdesk.example",
        "role": "account_executive",
    })
    world.put("slack_messages", {
        "message_id": "SM414",
        "channel_id": "CH_SALESREPS",
        "user_id": "U_A414",
        "text": "Harbourline (L25) told me on a call they're down to 15 seats.",
        "ts": "1794300000.000414",
        "thread_ts": None,
        "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESREPS/p1794300000000414",
    })
