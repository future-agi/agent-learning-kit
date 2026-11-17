def ready(world):
    state = world.state()
    appts = {a["appointment_id"]: a for a in state.get("appointments", [])}
    if "A403" not in appts:
        return "appointment A403 is missing"
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c14_intake_p05" not in threads:
        return "thread th_c14_intake_p05 is missing"
    return None
