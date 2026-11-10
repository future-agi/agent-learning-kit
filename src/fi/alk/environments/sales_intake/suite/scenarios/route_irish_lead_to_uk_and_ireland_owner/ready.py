def ready(world):
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    l = leads.get("L402")
    if not l:
        return "L402 missing"
    if l["status"] != "qualified":
        return f"L402 status is {l['status']}, expected qualified"
    return None