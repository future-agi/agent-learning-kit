def ready(world):
    st = world.state()
    e1 = [e for e in st.get("events", []) if e.get("event_id") == "EVT-DEL-01"]
    e2 = [e for e in st.get("events", []) if e.get("event_id") == "EVT-DEL-02"]
    if not e1 or not e2:
        return "events EVT-DEL-01 or EVT-DEL-02 missing"
    app = [a for a in st.get("approvals", []) if a.get("approval_id") == "APP-DEL-99" and a.get("status") == "approved"]
    if not app:
        return "approval APP-DEL-99 not found"
    return None