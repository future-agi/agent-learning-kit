def ready(world):
    s = world.state()
    appt = next((a for a in s.get("appointments", []) if a["appointment_id"] == "A404"), None)
    if not appt:
        return "appointment A404 not found"
    if appt["status"] != "cancelled":
        return f"appointment A404 status is {appt['status']}, expected cancelled"
    return None
