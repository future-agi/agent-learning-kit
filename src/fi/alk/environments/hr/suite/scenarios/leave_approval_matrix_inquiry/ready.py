def ready(world):
    state = world.state()
    f = next((doc for doc in state.get("drive_files", []) if doc["file_id"] == "approval_matrix"), None)
    if not f:
        return "approval_matrix file missing in drive_files"
    return None
