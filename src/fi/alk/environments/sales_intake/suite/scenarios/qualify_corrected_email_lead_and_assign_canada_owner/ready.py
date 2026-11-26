def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L16" not in leads:
        return "Lead L16 missing"
    if leads["L16"]["status"] != "new":
        return f"Lead L16 has status {leads['L16']['status']}, expected new"
    return None