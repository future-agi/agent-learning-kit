def ready(world):
    p = next((x for x in world.state()["pages"] if x["page_id"] == "N-01"), None)
    if not p:
        return "page N-01 not found"
    return None