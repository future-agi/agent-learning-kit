def ready(world):
    appts = world.state().get("appointments", [])
    a11 = next((a for a in appts if a["appointment_id"] == "A11"), None)
    if not a11 or a11["status"] != "scheduled":
        return "appointment A11 is missing or not scheduled"
    return None