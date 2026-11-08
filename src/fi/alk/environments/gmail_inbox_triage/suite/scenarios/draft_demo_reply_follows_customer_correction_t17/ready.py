def ready(world):
    state = world.state()
    theirs = sorted((m for m in state.get("gmail_messages", []) if m["thread_id"] == "T-17" and m["sender"] == "kevin@lead.com"), key=lambda m: m["date"])
    if len(theirs) < 2 or "Thursday" not in theirs[-1]["body"] or "Tuesday" not in theirs[0]["body"]:
        return "T-17 does not hold Kevin's Tuesday request followed by his Thursday correction"
    return None
