def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    l = leads.get("L26")
    if not l:
        return "L26 missing"
    if l["status"] != "qualified":
        return f"L26 status is {l['status']}, expected qualified"
    return None