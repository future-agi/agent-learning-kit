def ready(world):
    s = world.state()
    if not any(c["customer_id"] == "CU404" for c in s.get("customers", [])):
        return "CU404 missing"
    if len([a for a in s.get("appointments", []) if a["staff_id"] == "ST01" and a["local_start"].startswith("2026-11-05")]) != 12:
        return "Olivia schedule not fully booked on 2026-11-05"
    return None
