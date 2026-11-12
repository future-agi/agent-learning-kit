def ready(world):
    appointments = world.state()["appointments"]
    app = next((a for a in appointments if a["appointment_id"] == "A422"), None)
    if not app:
        return "Appointment A422 missing"
    return None