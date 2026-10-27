def ready(world):
    page = next((p for p in world.state()["pages"] if p["page_id"] == "N-04"), None)
    if not page:
        return "page N-04 not found"
    return None