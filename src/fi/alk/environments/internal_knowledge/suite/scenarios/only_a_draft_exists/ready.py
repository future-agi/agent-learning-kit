def ready(world):
    f = next((x for x in world.state()["drive_files"] if x["file_id"] == "ai_tools_v1_draft"), None)
    if not f:
        return "draft file not found"
    return None