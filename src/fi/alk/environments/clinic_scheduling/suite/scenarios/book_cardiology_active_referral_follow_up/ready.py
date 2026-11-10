def ready(world):
    p = next((r for r in world.state().get("patients", []) if r["patient_id"] == "P082"), None)
    if not p:
        return "patient P082 not found"
    ref = next((r for r in world.state().get("referrals", []) if r["patient_id"] == "P082" and r["specialty"] == "cardiology" and r["status"] == "active"), None)
    if not ref:
        return "active cardiology referral for P082 not found"
    return None
