def ready(world):
    s = world.state()
    leads = {l["lead_id"]: l for l in s.get("leads", [])}
    if "L03" not in leads:
        return "L03 lead missing"
    if leads["L03"].get("owner_rep_id") != "A02":
        return f"L03 owner_rep_id is {leads['L03'].get('owner_rep_id')}, expected A02"
    return None
