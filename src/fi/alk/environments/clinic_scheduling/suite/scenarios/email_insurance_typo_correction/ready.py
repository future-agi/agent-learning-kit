def ready(world):
    state = world.state()
    threads = {t["id"]: t for t in state.get("gmail_threads", [])}
    if "th_c09_insurance_p19" not in threads:
        return "thread th_c09_insurance_p19 is missing"
    patients = {p["patient_id"]: p for p in state.get("patients", [])}
    if "P19" not in patients:
        return "patient P19 is missing"
    return None
