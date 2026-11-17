def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    l = leads.get("L01")
    if not l:
        return "L01 missing"
    if l["status"] != "new":
        return f"L01 status is {l['status']}, expected new"
    return None