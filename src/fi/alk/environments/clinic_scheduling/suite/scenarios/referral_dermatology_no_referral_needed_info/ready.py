def ready(world):
    derm = [s for s in world.state()["specialties"] if s["specialty"] == "dermatology"]
    if not derm or derm[0]["referral_required"] != 0:
        return "Dermatology specialty should not require referral"
    prep = [p for p in world.state()["preparation_instructions"] if p["specialty"] == "dermatology"]
    if not prep:
        return "Dermatology preparation instructions missing"
    return None