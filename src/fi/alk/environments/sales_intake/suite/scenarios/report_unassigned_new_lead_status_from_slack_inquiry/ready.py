def ready(world):
    msgs = {m["message_id"]: m for m in world.state().get("slack_messages", [])}
    if "SM03" not in msgs:
        return "Slack message SM03 missing"
    leads = {l["lead_id"]: l for l in world.state().get("leads", [])}
    if "L01" not in leads:
        return "Lead L01 missing"
    return None