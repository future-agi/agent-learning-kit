def ready(world):
    state = world.state()
    f1 = next((doc for doc in state.get("drive_files", []) if doc["file_id"] == "pol_uk_annual_2023"), None)
    f2 = next((doc for doc in state.get("drive_files", []) if doc["file_id"] == "pol_uk_annual_2026"), None)
    if not f1 or not f2:
        return "Required UK policy files missing in drive_files"
    return None
