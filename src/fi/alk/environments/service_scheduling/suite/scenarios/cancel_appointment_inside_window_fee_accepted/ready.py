def ready(world):
    appts = [a for a in world.state()["appointments"] if a["appointment_id"] == "A423"]
    if not appts or appts[0]["status"] != "booked":
        return "Appointment A423 missing or not booked"
    return None