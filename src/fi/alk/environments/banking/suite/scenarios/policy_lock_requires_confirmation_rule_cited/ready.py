def ready(world):
    files = {f["file_id"]: f for f in world.state().get("drive_files", [])}
    if "pol_card_v2" not in files:
        return "pol_card_v2 missing"
    return None
