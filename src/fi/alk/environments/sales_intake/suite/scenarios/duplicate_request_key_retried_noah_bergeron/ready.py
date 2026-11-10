def ready(world):
    s = world.state()
    leads = {l["lead_id"]: l for l in s.get("leads", [])}
    if "L16" not in leads:
        return "L16 lead missing"
    return None