def ready(world):
    appts = world.state().get("appointments", [])
    a409 = next((a for a in appts if a["appointment_id"] == "A409"), None)
    if not a409 or a409["status"] != "scheduled":
        return "appointment A409 is missing or not scheduled"
    return None