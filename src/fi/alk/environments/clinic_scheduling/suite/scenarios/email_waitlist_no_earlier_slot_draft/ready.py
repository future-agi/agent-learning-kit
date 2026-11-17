def ready(world):
    state = world.state()
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c12_waitlist_p03" not in threads:
        return "thread th_c12_waitlist_p03 is missing"
    appts = {a["appointment_id"]: a for a in state.get("appointments", [])}
    if "A10" not in appts or appts["A10"]["patient_id"] != "P03":
        return "appointment A10 is not for patient P03"
    return None
