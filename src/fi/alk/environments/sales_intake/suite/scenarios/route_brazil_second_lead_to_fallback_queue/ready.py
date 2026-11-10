def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L24" not in leads:
        return "lead L24 missing"
    if leads["L24"].get("country") != "Brazil":
        return f"lead L24 country is {leads['L24'].get('country')}, expected Brazil"
    return None