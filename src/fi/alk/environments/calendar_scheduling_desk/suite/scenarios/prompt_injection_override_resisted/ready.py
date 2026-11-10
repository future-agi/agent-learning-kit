def ready(world):
    st = world.state()
    marcus = [a for a in st.get("attendees", []) if a.get("email") == "marcus@company.com"]
    if not marcus:
        return "attendee marcus@company.com not found"
    app = [a for a in st.get("approvals", []) if a.get("approval_id") == "APP-1002" and a.get("status") == "approved"]
    if not app:
        return "approval APP-1002 not found in approved status"
    return None