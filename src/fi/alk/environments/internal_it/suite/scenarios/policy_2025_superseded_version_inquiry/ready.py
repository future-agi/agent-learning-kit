def ready(world):
    state = world.state()
    f2 = next((d for d in state.get("drive_files", []) if d["file_id"] == "acc_pol_v2"), None)
    f3 = next((d for d in state.get("drive_files", []) if d["file_id"] == "acc_pol_v3"), None)
    if not f2 or not f3:
        return "missing policy document acc_pol_v2 or acc_pol_v3"
    return None
