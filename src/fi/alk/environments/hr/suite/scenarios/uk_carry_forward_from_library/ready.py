def ready(world):
    state = world.state()
    f = next((doc for doc in state.get("drive_files", []) if doc["file_id"] == "pol_uk_annual_2026"), None)
    if not f:
        return "pol_uk_annual_2026 file missing in drive_files"
    return None
