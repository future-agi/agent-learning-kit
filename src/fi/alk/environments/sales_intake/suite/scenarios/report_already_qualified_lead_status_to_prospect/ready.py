def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    l = leads.get("L14")
    if not l:
        return "L14 missing"
    if l["status"] != "qualified" or l["owner_rep_id"] != "A04":
        return f"L14 status/owner is {l['status']}/{l['owner_rep_id']}, expected qualified/A04"
    return None