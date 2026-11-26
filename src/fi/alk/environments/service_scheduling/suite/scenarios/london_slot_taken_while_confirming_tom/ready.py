def ready(world):
    s = world.state()
    bc = next((c for c in s.get("booking_contention", []) if c["contention_id"] == "BC403"), None)
    if not bc:
        return "BC403 booking contention not found"
    return None