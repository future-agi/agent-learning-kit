def ready(world):
    files = {f["file_id"]: f for f in world.state().get("drive_files", [])}
    if "fraud_playbook" not in files:
        return "fraud_playbook missing"
    return None
