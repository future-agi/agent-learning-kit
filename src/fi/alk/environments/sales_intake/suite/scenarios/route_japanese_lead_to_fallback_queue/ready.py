def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L12" not in leads:
        return "lead L12 missing"
    if leads["L12"].get("country") != "Japan":
        return f"lead L12 country is {leads['L12'].get('country')}, expected Japan"
    return None