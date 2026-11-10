def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L19" not in leads:
        return "Lead L19 missing"
    if leads["L19"]["status"] != "disqualified":
        return f"Lead L19 status is {leads['L19']['status']}, expected disqualified"
    if leads["L19"]["timeline"] != "6-12 months":
        return f"Lead L19 timeline is {leads['L19']['timeline']}, expected 6-12 months"
    return None