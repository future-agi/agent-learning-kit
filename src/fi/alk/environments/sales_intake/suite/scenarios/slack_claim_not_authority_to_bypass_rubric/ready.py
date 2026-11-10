def ready(world):
    s = world.state()
    leads = {l["lead_id"]: l for l in s.get("leads", [])}
    if "L08" not in leads:
        return "L08 lead missing"
    msgs = s.get("slack_messages", [])
    if not any(m.get("message_id") == "SM02" for m in msgs):
        return "SM02 slack message missing"
    return None
