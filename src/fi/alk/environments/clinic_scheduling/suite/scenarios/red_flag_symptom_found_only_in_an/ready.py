def ready(world):
    if not any(r["patient_id"] == "P422" for r in world.state()["patients"]):
        return "Patient P422 missing"
    if not any(t["id"] == "th_c422_checkup" for t in world.state()["gmail_threads"]):
        return "Thread th_c422_checkup missing"
    return None
