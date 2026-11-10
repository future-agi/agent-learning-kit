def setup(world):
    world.put("messages", {
        "message_id": "M01",
        "channel_id": "C01",
        "user_id": "U03",
        "text": "Initial proposal: Target launch date set for Friday November 1st.",
        "ts": "1729840000.000100",
        "thread_ts": "1729840000.000100",
        "permalink": "https://slack.example.com/archives/C01/p1729840000000100"
    })
    world.put("messages", {
        "message_id": "M02",
        "channel_id": "C01",
        "user_id": "U01",
        "text": "CORRECTION / UPDATE: Due to QA testing requirements, the final launch date is confirmed for Monday November 4th.",
        "ts": "1729843600.000200",
        "thread_ts": "1729840000.000100",
        "permalink": "https://slack.example.com/archives/C01/p1729843600000200"
    })
