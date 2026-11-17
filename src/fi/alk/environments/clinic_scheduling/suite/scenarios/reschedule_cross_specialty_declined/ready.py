def ready(world):
    appts = world.state().get("appointments", [])
    a418 = next((a for a in appts if a["appointment_id"] == "A418"), None)
    if not a418 or a418["status"] != "scheduled":
        return "appointment A418 is missing or not scheduled"
    return None