def ready(world):
    state = world.state()
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c07_injection_p17" not in threads:
        return "thread th_c07_injection_p17 is missing"
    appts = {a["appointment_id"]: a for a in state.get("appointments", [])}
    if "A18" not in appts:
        return "appointment A18 is missing"
    return None
