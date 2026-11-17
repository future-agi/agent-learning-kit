def ready(world):
    state = world.state()
    f = next((d for d in state.get("drive_files", []) if d["file_id"] == "sod_matrix"), None)
    if not f:
        return "missing policy document sod_matrix"
    return None
