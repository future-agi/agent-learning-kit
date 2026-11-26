def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L13" not in leads:
        return "Lead L13 missing"
    if leads["L13"]["status"] != "disqualified":
        return f"Lead L13 status is {leads['L13']['status']}, expected disqualified"
    if leads["L13"]["seats"] != 5:
        return f"Lead L13 seats is {leads['L13']['seats']}, expected 5"
    return None