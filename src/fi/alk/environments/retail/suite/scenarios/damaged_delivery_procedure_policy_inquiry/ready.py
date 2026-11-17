def ready(world):
    files = world.state().get("drive_files", [])
    if not any(f["file_id"] == "damage_procedure" for f in files):
        return "damage_procedure file not found in drive"
    return None
