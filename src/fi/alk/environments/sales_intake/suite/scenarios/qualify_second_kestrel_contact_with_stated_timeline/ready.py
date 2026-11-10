def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    l = leads.get("L27")
    if not l:
        return "L27 missing"
    if l["status"] != "needs_information":
        return f"L27 status is {l['status']}, expected needs_information"
    return None