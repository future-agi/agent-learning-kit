def ready(world):
    appointments = world.state()["appointments"]
    app = next((a for a in appointments if a["appointment_id"] == "A421"), None)
    if not app:
        return "Appointment A421 missing"
    return None