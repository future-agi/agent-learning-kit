def ready(world):
    state = world.state()
    appts = {a["appointment_id"]: a for a in state.get("appointments", [])}
    a = appts.get("A07")
    if not a or a["status"] != "scheduled" or a["patient_id"] != "P14":
        return "appointment A07 is not scheduled for patient P14"
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c04_reschedule_p14" not in threads:
        return "thread th_c04_reschedule_p14 is missing"
    return None
