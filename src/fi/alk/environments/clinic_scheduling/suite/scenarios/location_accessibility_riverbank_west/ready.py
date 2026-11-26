def ready(world):
    state = world.state()
    loc = next((r for r in state.get("locations", []) if r.get("location_id") == 'LOC-WES'), None)
    if not loc:
        return "location LOC-WES is missing"
    return None
