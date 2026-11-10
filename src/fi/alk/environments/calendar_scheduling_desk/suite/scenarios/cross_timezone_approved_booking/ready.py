def ready(world):
    st = world.state()
    evts = [e for e in st.get("events", []) if e.get("event_id") == "EVT-BASE-01"]
    if not evts:
        return "EVT-BASE-01 missing from world"
    app = [a for a in st.get("approvals", []) if a.get("approval_id") == "APP-1001" and a.get("status") == "approved"]
    if not app:
        return "approval APP-1001 not found in approved status"
    return None