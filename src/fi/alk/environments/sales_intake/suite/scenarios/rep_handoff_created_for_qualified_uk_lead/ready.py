def ready(world):
    s = world.state()
    leads = {l["lead_id"]: l for l in s.get("leads", [])}
    if "L_BEATRICE" not in leads:
        return "L_BEATRICE lead missing"
    if leads["L_BEATRICE"].get("status") != "new":
        return f"L_BEATRICE status is {leads['L_BEATRICE'].get('status')}, expected new"
    return None
