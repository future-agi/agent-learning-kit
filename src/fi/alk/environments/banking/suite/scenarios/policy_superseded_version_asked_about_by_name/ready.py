def ready(world):
    files = {f["file_id"]: f for f in world.state().get("drive_files", [])}
    if "pol_dispute_v2" not in files:
        return "pol_dispute_v2 missing"
    if "pol_dispute_v1" not in files:
        return "pol_dispute_v1 missing"
    return None
