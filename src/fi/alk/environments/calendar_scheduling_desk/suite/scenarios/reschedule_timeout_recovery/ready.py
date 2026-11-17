def ready(world):
    st = world.state()
    evts = [e for e in st.get("calendar_events", []) if e.get("event_id") == "EVT-04"]
    if not evts:
        return "EVT-04 missing from world"
    faults = [f for f in st.get("calendar_fault_injections", []) if f.get("operation") == "update_event" and f.get("is_active")]
    if not faults:
        return "timeout fault injection missing for update_event"
    return None