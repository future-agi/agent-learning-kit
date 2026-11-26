def ready(world):
    appts = world.state().get("appointments", [])
    a401 = next((a for a in appts if a["appointment_id"] == "A401"), None)
    if not a401 or a401["status"] != "scheduled":
        return "appointment A401 is missing or not scheduled"
    return None