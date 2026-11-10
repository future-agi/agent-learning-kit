def ready(world):
    state = world.state()
    f_draft = next((d for d in state.get("drive_files", []) if d["file_id"] == "acc_pol_v4_draft"), None)
    f_eff = next((d for d in state.get("drive_files", []) if d["file_id"] == "acc_pol_v3"), None)
    if not f_draft or not f_eff:
        return "missing policy documents acc_pol_v4_draft or acc_pol_v3"
    return None
