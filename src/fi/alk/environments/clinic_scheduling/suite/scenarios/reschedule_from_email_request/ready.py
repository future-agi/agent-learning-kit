def ready(world):
    appts = world.state().get("appointments", [])
    a07 = next((a for a in appts if a["appointment_id"] == "A07"), None)
    if not a07 or a07["status"] != "scheduled":
        return "appointment A07 is missing or not scheduled"
    return None