def setup(world):
    world.put("slack_messages", {
        "channel_id": "CH_HRHELP",
        "message_id": "SM401",
        "permalink": "https://acme.slack.example.com/archives/CH_HRHELP/p1792600000000401",
        "text": "Hi HR, please submit an annual leave request for me for 30 November to 1 December 2026.",
        "thread_ts": None,
        "ts": "1792600000.000401",
        "user_id": "U_E02"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "HR01"}, by="id")
