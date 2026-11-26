def ready(world):
    appts = world.state().get("appointments", [])
    a = next((x for x in appts if x["appointment_id"] == "A21"), None)
    if not a or a["status"] != "scheduled":
        return "appointment A21 is not scheduled"
    return None