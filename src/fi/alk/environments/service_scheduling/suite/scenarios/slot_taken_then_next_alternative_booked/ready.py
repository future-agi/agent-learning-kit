def ready(world):
    s = world.state()
    bc = next((c for c in s.get("booking_contention", []) if c["contention_id"] == "BC404"), None)
    if not bc:
        return "BC404 contention missing"
    return None