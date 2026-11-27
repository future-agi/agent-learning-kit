def ready(world):
    appointments = world.state()["appointments"]
    app = next((a for a in appointments if a["appointment_id"] == "A420"), None)
    if not app:
        return "Appointment A420 missing"
    return None