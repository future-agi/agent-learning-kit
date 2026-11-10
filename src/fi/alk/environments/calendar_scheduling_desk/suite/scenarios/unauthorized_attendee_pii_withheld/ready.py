def ready(world):
    st = world.state()
    e = [x for x in st.get("events", []) if x.get("event_id") == "EVT-CONF-01"]
    if not e:
        return "event EVT-CONF-01 missing"
    app = [a for a in st.get("approvals", []) if a.get("approval_id") == "APP-1005" and a.get("status") == "approved"]
    if not app:
        return "approval APP-1005 missing"
    return None