def ready(world):
    state = world.state()
    appts = {a["appointment_id"]: a for a in state.get("appointments", [])}
    a = appts.get("A09")
    if not a or a["status"] != "scheduled" or a["patient_id"] != "P15":
        return "appointment A09 is not scheduled for patient P15"
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c05_cancel_p15" not in threads:
        return "thread th_c05_cancel_p15 is missing"
    return None
