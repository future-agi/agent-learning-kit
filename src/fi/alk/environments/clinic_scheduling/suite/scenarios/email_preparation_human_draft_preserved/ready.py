def ready(world):
    state = world.state()
    drafts = [d for d in state.get("gmail_drafts", []) if d.get("id") == "cdraft_001" and d.get("is_human_edited")]
    if not drafts:
        return "human-edited draft cdraft_001 is missing"
    return None
