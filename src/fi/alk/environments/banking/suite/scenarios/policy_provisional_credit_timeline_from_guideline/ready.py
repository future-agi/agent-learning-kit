def ready(world):
    files = {f["file_id"]: f for f in world.state().get("drive_files", [])}
    if "pol_provisional_credit" not in files:
        return "pol_provisional_credit missing"
    return None
