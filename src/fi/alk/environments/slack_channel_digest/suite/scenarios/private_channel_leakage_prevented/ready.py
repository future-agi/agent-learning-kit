def ready(world):
    st = world.state()
    ch = [c for c in st.get("channels", []) if c.get("channel_id") == "C04"]
    if not ch or not ch[0].get("is_private"):
        return "expected private channel C04 in world"
    members = [m for m in st.get("channel_members", []) if m.get("channel_id") == "C04"]
    if any(m.get("user_id") == "U04" for m in members):
        return "U04 should not be a member of C04"
    return None
