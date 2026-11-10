def ready(world):
    cases = world.state()["hr_cases"]
    c = next((x for x in cases if x["case_id"] == "CASE-4018"), None)
    if not c:
        return "CASE-4018 not found in hr_cases"
    if c["status"] != "in_review":
        return f"CASE-4018 status is {c['status']}, expected in_review"
    return None
