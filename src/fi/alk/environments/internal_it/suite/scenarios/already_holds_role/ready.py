def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E11" and e["status"] == "active"), None)
    if not emp:
        return "employee E11 not found or inactive"
    ent = next((e for e in state.get("entitlements", []) if e["employee_id"] == "E11" and e["app_id"] == "APP-CRM" and e["role_name"] == "sales_rep" and e["status"] == "active"), None)
    if not ent:
        return "active entitlement ENT-105 for E11 on APP-CRM sales_rep missing"
    return None
