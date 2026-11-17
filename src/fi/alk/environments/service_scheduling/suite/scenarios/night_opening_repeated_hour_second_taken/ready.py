def ready(world):
    s = world.state()
    loc = next((l for l in s.get("locations", []) if l["location_id"] == "L1"), None)
    if not loc or loc["sunday_open"] != "00:00":
        return "Midtown has no Sunday night opening"
    if not any(a["appointment_id"] == "A453" and a["start_utc"] == "2026-11-01T06:30:00Z" and a["status"] == "booked" for a in s.get("appointments", [])):
        return "the second 1:30 is not taken"
    return None
