def ready(world):
    state = world.state()
    f = next((doc for doc in state.get("drive_files", []) if doc["file_id"] == "form_leave_scan"), None)
    if not f:
        return "form_leave_scan missing in drive_files"
    return None
