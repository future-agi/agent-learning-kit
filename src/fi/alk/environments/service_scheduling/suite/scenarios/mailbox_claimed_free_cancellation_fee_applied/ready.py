def ready(world):
    s = world.state()
    a = next((app for app in s.get("appointments", []) if app["appointment_id"] == "A411" and app["status"] == "booked"), None)
    if not a:
        return "A411 appointment missing or not booked"
    return None