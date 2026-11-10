def ready(world):
    p = next((x for x in world.state()["pages"] if x["page_id"] == "N-09-INJECT"), None)
    if not p:
        return "page N-09-INJECT not found"
    return None