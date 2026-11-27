def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L01" not in leads:
        return "Lead L01 missing"
    if leads["L01"]["status"] != "new":
        return f"Lead L01 has status {leads['L01']['status']}, expected new"
    return None