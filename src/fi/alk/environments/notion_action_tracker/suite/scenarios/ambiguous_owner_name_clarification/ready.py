def ready(world):
    page = next((p for p in world.state()["pages"] if p["page_id"] == "N-06"), None)
    if not page:
        return "page N-06 not found"
    return None