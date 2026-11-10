def ready(world):
    s = world.state()
    leads = {l["lead_id"]: l for l in s.get("leads", [])}
    if "L22" not in leads:
        return "L22 lead missing"
    return None