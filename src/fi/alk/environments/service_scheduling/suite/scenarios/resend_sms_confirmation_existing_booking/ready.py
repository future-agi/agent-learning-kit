def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU415" for c in s.get("customers", [])):
        return "CU415 missing"
    if not any(a["appointment_id"] == "A415" for a in s.get("appointments", [])):
        return "A415 missing"
    return None
