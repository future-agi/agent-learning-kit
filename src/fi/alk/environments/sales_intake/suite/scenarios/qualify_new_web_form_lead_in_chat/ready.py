def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L401" not in leads:
        return "Lead L401 missing"
    if leads["L401"]["status"] != "new":
        return f"Lead L401 status is {leads['L401']['status']}, expected new"
    return None