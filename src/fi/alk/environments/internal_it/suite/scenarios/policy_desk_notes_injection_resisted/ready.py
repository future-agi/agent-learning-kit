def ready(world):
    state = world.state()
    fn = next((d for d in state.get("drive_files", []) if d["file_id"] == "it_desk_notes"), None)
    fe = next((d for d in state.get("drive_files", []) if d["file_id"] == "acc_pol_v3"), None)
    if not fn or not fe:
        return "missing policy documents it_desk_notes or acc_pol_v3"
    return None
