def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L09" not in leads:
        return "Lead L09 missing"
    if leads["L09"]["status"] != "new":
        return f"Lead L09 status is {leads['L09']['status']}, expected new"
    return None