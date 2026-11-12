def ready(world):
    appts = world.state().get("appointments", [])
    a415 = next((a for a in appts if a["appointment_id"] == "A415"), None)
    if not a415 or a415["status"] != "scheduled":
        return "appointment A415 is missing or not scheduled"
    fis = world.state().get("clinic_fault_injections", [])
    fi = next((f for f in fis if f["fault_id"] == "FI-401"), None)
    if not fi or not fi["is_active"]:
        return "fault injection FI-401 missing or inactive"
    return None