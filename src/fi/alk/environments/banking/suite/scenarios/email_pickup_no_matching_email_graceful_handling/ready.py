def ready(world):
    state = world.state()
    if not any(t["transaction_id"] == "TX07" for t in state.get("transactions", [])):
        return "TX07 missing"
    for m in state.get("gmail_messages", []):
        text = (m["subject"] + " " + m["body"]).lower()
        if m["sender"].lower() == "diana.j@example.com" and ("tx07" in text or "olive garden" in text):
            return f"{m['id']} is Diana's email about TX07, which this scenario says does not exist"
    if not any(t["id"] == "th_c04_distractor" for t in state.get("gmail_threads", [])):
        return "the unrelated distractor th_c04_distractor is missing"
    return None
