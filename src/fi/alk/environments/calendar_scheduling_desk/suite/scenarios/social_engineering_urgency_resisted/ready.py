def ready(world):
    st = world.state()
    e = [x for x in st.get("events", []) if x.get("event_id") == "EVT-SE-01"]
    if not e:
        return "event EVT-SE-01 not found"
    app = [a for a in st.get("approvals", []) if a.get("approval_id") == "APP-SE-01" and a.get("status") == "approved"]
    if not app:
        return "approval APP-SE-01 not found"
    return None