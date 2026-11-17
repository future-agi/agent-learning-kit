def ready(world):
    files = {f["file_id"]: f for f in world.state().get("drive_files", [])}
    if "form_dispute_affidavit" not in files:
        return "form_dispute_affidavit missing"
    return None
