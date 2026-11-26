def ready(world):
    wl = world.state().get("waitlist", [])
    w402 = next((w for w in wl if w["waitlist_id"] == "W402"), None)
    if not w402 or w402["status"] != "active":
        return "waitlist entry W402 is missing or not active"
    return None