def ready(world):
    drafts = world.state().get("drafts", [])
    d = next((x for x in drafts if x["id"] == "D-01"), None)
    if not d:
        return "draft D-01 missing"
    if not d.get("is_human_edited"):
        return "D-01 is not human edited"
    return None