def ready(world):
    s = world.state()
    leads = {l["lead_id"]: l for l in s.get("leads", [])}
    if "L06" not in leads:
        return "L06 lead missing"
    return None