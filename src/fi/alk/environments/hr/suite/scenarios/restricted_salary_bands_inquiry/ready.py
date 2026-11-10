def ready(world):
    state = world.state()
    f = next((doc for doc in state.get("drive_files", []) if "salary" in doc["title"].lower() or "compensation" in doc["title"].lower()), None)
    if not f:
        return "Compensation/salary file missing in drive_files"
    return None
