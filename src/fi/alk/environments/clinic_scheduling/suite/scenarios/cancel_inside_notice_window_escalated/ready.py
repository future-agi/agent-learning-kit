def ready(world):
    appts = world.state().get("appointments", [])
    a411 = next((a for a in appts if a["appointment_id"] == "A411"), None)
    if not a411 or a411["status"] != "scheduled":
        return "appointment A411 is missing or not scheduled"
    return None