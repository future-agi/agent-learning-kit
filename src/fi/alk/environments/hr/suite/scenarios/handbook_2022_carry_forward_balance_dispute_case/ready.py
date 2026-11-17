def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E431"), None)
    if not emp:
        return "E431 missing"
    file = next((f for f in state.get("drive_files", []) if f["file_id"] == "handbook_2022"), None)
    if not file:
        return "handbook_2022 missing"
    return None
