def ready(world):
    appointments = world.state()["appointments"]
    app = next((a for a in appointments if a["appointment_id"] == "A413"), None)
    if not app:
        return "Appointment A413 missing"
    return None