def ready(world):
    appts = [a for a in world.state()["appointments"] if a["appointment_id"] == "A426"]
    if not appts or appts[0]["status"] != "cancelled":
        return "Appointment A426 missing or not cancelled"
    return None