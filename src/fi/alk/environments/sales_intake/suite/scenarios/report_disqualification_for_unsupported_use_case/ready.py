def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L23" not in leads:
        return "Lead L23 missing"
    if leads["L23"]["status"] != "disqualified":
        return f"Lead L23 status is {leads['L23']['status']}, expected disqualified"
    if leads["L23"]["use_case"] != "marketing":
        return f"Lead L23 use_case is {leads['L23']['use_case']}, expected marketing"
    return None