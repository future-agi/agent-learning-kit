def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E02'), None)
    if not row or row.get("status") != 'active':
        return "employees E02 is missing or status is not active"
    doc = next((d for d in state.get("drive_files", []) if d.get("file_id") == 'leave_v2'), None)
    if not doc:
        return "drive_file leave_v2 is missing"
    return None
