def ready(world):
    state = world.state()
    f = next((d for d in state.get("drive_files", []) if d["file_id"] == "temp_access_policy"), None)
    if not f:
        return "missing policy document temp_access_policy"
    return None
