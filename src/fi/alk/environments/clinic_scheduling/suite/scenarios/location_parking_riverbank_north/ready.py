def ready(world):
    state = world.state()
    loc = next((r for r in state.get("locations", []) if r.get("location_id") == 'LOC-NOR'), None)
    if not loc:
        return "location LOC-NOR is missing"
    return None
