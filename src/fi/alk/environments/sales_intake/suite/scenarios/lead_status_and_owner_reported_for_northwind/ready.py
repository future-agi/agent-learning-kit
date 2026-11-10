def ready(world):
    s = world.state()
    leads = {l["lead_id"]: l for l in s.get("leads", [])}
    if "L01" not in leads:
        return "L01 lead missing"
    if leads["L01"].get("owner_rep_id") is not None:
        return "L01 unexpectedly has owner_rep_id set"
    return None
