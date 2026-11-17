def ready(world):
    appts = world.state().get("appointments", [])
    a407 = next((a for a in appts if a["appointment_id"] == "A407"), None)
    if not a407 or a407["status"] != "scheduled":
        return "appointment A407 is missing or not scheduled"
    return None