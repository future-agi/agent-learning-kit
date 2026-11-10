def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L17" not in leads:
        return "lead L17 missing from leads table"
    l = leads["L17"]
    if l.get("country") != "South Africa":
        return f"lead L17 country is {l.get('country')}, expected South Africa"
    return None