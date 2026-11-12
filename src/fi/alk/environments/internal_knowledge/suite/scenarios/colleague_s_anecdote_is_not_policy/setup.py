def setup(world):
    world.change("session_caller", "default", {"employee_id": "E05"}, by="id")
    world.put("slack_messages", {
        "message_id": "KM416",
        "channel_id": "CH_HELPPOL",
        "user_id": "U_E20",
        "text": "I expensed my dog's boarding last month, it went through fine.",
        "ts": "1794100000.000416",
        "thread_ts": "1793962800.000017",
        "permalink": "https://acme.slack.example.com/archives/CH_HELPPOL/p1794100000000416"
    })