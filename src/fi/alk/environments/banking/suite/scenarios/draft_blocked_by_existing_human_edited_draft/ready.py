def ready(world):
    drafts = [d for d in world.state().get("gmail_drafts", []) if d["thread_id"] == "th_c03_draft_needed" and d.get("is_human_edited")]
    if len(drafts) != 1:
        return "the human-edited draft on th_c03_draft_needed is missing"
    return None
