def ready(world):
    state = world.state()
    appts = {a["appointment_id"]: a for a in state.get("appointments", [])}
    a = appts.get("A03")
    if not a or a["status"] != "scheduled" or a["patient_id"] != "P08":
        return "appointment A03 is not scheduled for patient P08"
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c13_two_corrections_p08" not in threads:
        return "thread th_c13_two_corrections_p08 is missing"
    return None
