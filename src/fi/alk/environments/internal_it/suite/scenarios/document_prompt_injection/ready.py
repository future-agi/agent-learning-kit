def ready(world):
    state = world.state()
    f1 = next((f for f in state.get("drive_files", []) if f["file_id"] == "it_desk_notes"), None)
    f2 = next((f for f in state.get("drive_files", []) if f["file_id"] == "acc_pol_v3"), None)
    if not (f1 and f2):
        return "drive files it_desk_notes or acc_pol_v3 missing"
    return None
