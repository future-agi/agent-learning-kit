def ready(world):
    appts = world.state().get("appointments", [])
    a = next((x for x in appts if x["appointment_id"] == "A22"), None)
    if not a or a["status"] != "scheduled":
        return "appointment A22 is not scheduled"
    return None