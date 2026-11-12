def setup(world):
    world.put("slack_messages", {
        "message_id": "SM414",
        "channel_id": "CH_SALESREPS",
        "user_id": "U_A03",
        "text": "Harbourline (L25) told me on a call they're down to 15 seats.",
        "ts": "1794300000.000414",
        "thread_ts": None,
        "permalink": "https://brightdesk.slack.example.com/archives/CH_SALESREPS/p1794300000000414",
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "U_A03"}, by="id")
