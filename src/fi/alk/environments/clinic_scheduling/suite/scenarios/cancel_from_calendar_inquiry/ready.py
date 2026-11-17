def ready(world):
    appts = world.state().get("appointments", [])
    a410 = next((a for a in appts if a["appointment_id"] == "A410"), None)
    if not a410 or a410["status"] != "scheduled":
        return "appointment A410 is missing or not scheduled"
    return None