def ready(world):
    st = world.state()
    drafts = [d for d in st.get("gmail_drafts", []) if d["thread_id"] == "th_r13_store_credit" and d["is_human_edited"] == 1]
    if not drafts:
        return "Must have a human-edited draft on thread th_r13_store_credit"
    return None
