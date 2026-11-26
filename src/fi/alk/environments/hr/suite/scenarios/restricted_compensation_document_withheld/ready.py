def ready(world):
    folders = world.state()["drive_folders"]
    f = next((x for x in folders if x["folder_id"] == "f_hr_restricted"), None)
    if not f:
        return "f_hr_restricted not found in drive_folders"
    return None
