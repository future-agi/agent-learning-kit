def ready(world):
    pols = world.state()["hr_policies"]
    apac_pol = next((p for p in pols if p["region"] == "APAC" and p["topic"] == "annual_leave"), None)
    if not apac_pol:
        return "APAC annual leave policy not found in hr_policies"
    return None
