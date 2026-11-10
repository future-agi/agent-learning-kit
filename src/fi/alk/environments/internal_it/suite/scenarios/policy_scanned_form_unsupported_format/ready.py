def ready(world):
    state = world.state()
    f = next((d for d in state.get("drive_files", []) if d["file_id"] == "form_access_scan"), None)
    if not f:
        return "missing policy document form_access_scan"
    return None
