def ready(world):
    files = {f["file_id"]: f for f in world.state().get("drive_files", [])}
    if "pol_card_v2" not in files:
        return "pol_card_v2 missing"
    if "fee_sched_v4" not in files:
        return "fee_sched_v4 missing"
    return None
