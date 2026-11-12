def ready(world):
    state = world.state()
    row = next((r for r in state.get("employees", []) if r.get("employee_id") == 'E13'), None)
    if not row or row.get("status") != 'active':
        return "employees E13 is missing or its status is not active"
    f = next((doc for doc in state.get("drive_files", []) if doc.get("file_id") == 'salary_bands_2026'), None)
    if not f:
        return "drive_file salary_bands_2026 is missing"
    return None
