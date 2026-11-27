def ready(world):
    appts = world.state().get("appointments", [])
    a03 = next((a for a in appts if a["appointment_id"] == "A03"), None)
    if not a03 or a03["status"] != "scheduled":
        return "appointment A03 is missing or not scheduled"
    return None