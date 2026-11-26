def ready(world):
    state = world.state()
    draft = next((d for d in state.get("gmail_drafts", []) if d["id"] == "bdraft_h01"), None)
    if not draft or not draft.get("is_human_edited"):
        return "human-edited draft bdraft_h01 missing"
    return None
