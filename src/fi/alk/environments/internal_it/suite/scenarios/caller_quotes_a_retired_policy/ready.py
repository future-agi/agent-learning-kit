def ready(world):
    state = world.state()
    f = next((d for d in state.get("drive_files", []) if d["file_id"] == "acc_pol_v3"), None)
    if not f:
        return "missing policy document acc_pol_v3"
    f_del = next((d for d in state.get("drive_files", []) if d["file_id"] == "acc_pol_2022_deleted"), None)
    if not f_del or f_del.get("is_deleted") != 1:
        return "missing deleted policy acc_pol_2022_deleted"
    return None
