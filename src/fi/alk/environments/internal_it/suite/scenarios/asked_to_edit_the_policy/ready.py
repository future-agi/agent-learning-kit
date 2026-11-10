def ready(world):
    state = world.state()
    f = next((d for d in state.get("drive_files", []) if d["file_id"] == "acc_pol_v3"), None)
    if not f:
        return "missing policy document acc_pol_v3"
    return None
