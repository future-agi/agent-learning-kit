def ready(world):
    appts = world.state().get("appointments", [])
    if not any(a.get("appointment_id") == "A440" for a in appts):
        return "missing A440"
    return None
