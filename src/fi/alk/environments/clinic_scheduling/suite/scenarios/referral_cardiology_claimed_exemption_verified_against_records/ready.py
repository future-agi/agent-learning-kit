def ready(world):
    threads = world.state()["gmail_threads"]
    th = next((t for t in threads if t["id"] == "th_c06_claim_p16"), None)
    if not th:
        return "th_c06_claim_p16 missing"
    patients = world.state()["patients"]
    p = next((pt for pt in patients if pt["patient_id"] == "P16"), None)
    if not p or p["full_name"] != "Ravi Shah":
        return f"P16 mismatch: {p}"
    return None
