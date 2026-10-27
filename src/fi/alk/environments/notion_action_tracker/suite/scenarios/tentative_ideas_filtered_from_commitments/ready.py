def ready(world):
    page = next((p for p in world.state()["pages"] if p["page_id"] == "N-05"), None)
    if not page:
        return "page N-05 not found"
    return None