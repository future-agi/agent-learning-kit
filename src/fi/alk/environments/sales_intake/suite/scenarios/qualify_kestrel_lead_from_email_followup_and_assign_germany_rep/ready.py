def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    l = leads.get("L07")
    if not l:
        return "L07 missing"
    if l["status"] != "needs_information":
        return f"L07 status is {l['status']}, expected needs_information"
    return None