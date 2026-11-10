def ready(world):
    appts = world.state().get("appointments", [])
    a05 = next((a for a in appts if a["appointment_id"] == "A05"), None)
    if not a05 or a05["status"] != "scheduled":
        return "appointment A05 is missing or not scheduled"
    return None