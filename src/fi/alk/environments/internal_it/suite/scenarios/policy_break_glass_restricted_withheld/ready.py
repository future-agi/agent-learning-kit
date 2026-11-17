def ready(world):
    state = world.state()
    f = next((d for d in state.get("drive_files", []) if "playbook" in d.get("file_id", "").lower() or "secops" in d.get("file_id", "").lower()), None)
    if not f:
        return "missing restricted document"
    return None
