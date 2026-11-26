def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E427"), None)
    if not emp:
        return "missing employee E427"
    lic = next((l for l in state.get("app_licenses", []) if l["app_id"] == "APP-CRM"), None)
    if not lic:
        return "missing app licence record for APP-CRM"
    return None
