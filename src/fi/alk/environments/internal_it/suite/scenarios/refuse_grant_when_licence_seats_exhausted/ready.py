def ready(world):
    crm = [e for e in world.state().get("entitlements", []) if e["app_id"] == "APP-CRM" and e["status"] == "active"]
    if len(crm) < 3: return "CRM seats not full"
    return None
