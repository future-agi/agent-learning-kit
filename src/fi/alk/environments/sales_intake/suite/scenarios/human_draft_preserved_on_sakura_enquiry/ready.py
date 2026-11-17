def ready(world):
    s = world.state()
    drafts = s.get("gmail_drafts", [])
    if not any(d.get("id") == "D-HUMAN-12" and d.get("is_human_edited") for d in drafts):
        return "human draft D-HUMAN-12 missing or not human edited"
    return None
