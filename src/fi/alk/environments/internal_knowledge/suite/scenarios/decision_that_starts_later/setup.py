def setup(world):
    world.change("session_caller", "default", {"employee_id": "E16"}, by="id")
    world.put("slack_messages", {
        "message_id": "KM414",
        "channel_id": "CH_FINANCE",
        "user_id": "U_E04",
        "text": "Decision: the mileage rate goes to $0.70 a mile from 1 January 2027. Doc update pending.",
        "ts": "1794100000.000414",
        "thread_ts": None,
        "permalink": "https://acme.slack.example.com/archives/CH_FINANCE/p1794100000000414"
    })