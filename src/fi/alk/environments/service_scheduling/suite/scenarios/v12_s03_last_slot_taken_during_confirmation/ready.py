def ready(world):
    state = world.state()
    row = next((r for r in state.get("booking_contention", []) if r.get("contention_id") == 'BC401'), None)
    if not row or row.get("status") != 'armed':
        return "booking_contention BC401 is missing or its status is not armed"
    return None
