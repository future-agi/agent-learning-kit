def ready(world):
    s = world.state()
    l = next((loc for loc in s.get("locations", []) if loc["location_id"] == "L1"), None)
    if not l:
        return "L1 location not found"
    return None