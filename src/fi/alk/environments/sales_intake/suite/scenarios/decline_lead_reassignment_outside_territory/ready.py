def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L18" not in leads:
        return "Lead L18 missing"
    if leads["L18"]["owner_rep_id"] != "A06":
        return f"Lead L18 owner is {leads['L18']['owner_rep_id']}, expected A06"
    return None