def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    l = leads.get("L12")
    if not l:
        return "L12 missing"
    if l["status"] != "new":
        return f"L12 status is {l['status']}, expected new"
    return None