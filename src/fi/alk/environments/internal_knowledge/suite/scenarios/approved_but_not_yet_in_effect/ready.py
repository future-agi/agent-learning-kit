def ready(world):
    f = next((x for x in world.state()["drive_files"] if x["file_id"] == "per_diem_v3"), None)
    return None if f else "per_diem_v3 missing"