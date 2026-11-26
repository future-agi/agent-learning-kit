def ready(world):
    s = world.state()
    contacts = {c["contact_id"]: c for c in s.get("contacts", [])}
    if "C_DEVON" not in contacts:
        return "C_DEVON contact missing"
    leads = {l["lead_id"]: l for l in s.get("leads", [])}
    if "L_DEVON" not in leads:
        return "L_DEVON lead missing"
    return None
