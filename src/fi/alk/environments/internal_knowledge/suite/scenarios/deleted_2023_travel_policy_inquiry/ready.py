def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E17'), None)
    if not row or row.get("status") != 'active':
        return "employees E17 is missing or its status is not active"
    f = next((doc for doc in state.get("drive_files", []) if doc.get("file_id") == 'trv_policy_2023_deleted'), None)
    if not f or f.get("is_deleted") != 1:
        return "drive_file trv_policy_2023_deleted is missing or not marked deleted"
    return None
