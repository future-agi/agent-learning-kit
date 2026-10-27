def ready(world):
    st = world.state()
    msgs = [m for m in st.get("messages", []) if m.get("channel_id") == "C01"]
    if len(msgs) < 2:
        return "expected at least 2 messages in channel C01"
    apps = [a for a in st.get("approvals", []) if a.get("approval_id") == "APP-201"]
    if not apps:
        return "approval APP-201 missing"
    return None
