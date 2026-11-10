def ready(world):
    if not any(a["appointment_id"] == "A08" and a["status"] == "scheduled" for a in world.state()["appointments"]):
        return "Appointment A08 missing or not scheduled"
    return None
