def ready(world):
    s = world.state()
    bc = next((c for c in s.get("booking_contention", []) if c["contention_id"] == "BC405"), None)
    if not bc:
        return "BC405 contention missing"
    a = next((apt for apt in s.get("appointments", []) if apt["appointment_id"] == "A007"), None)
    if not a or a["status"] != "booked":
        return "A007 appointment not booked"
    return None