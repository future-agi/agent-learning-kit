def ready(world):
    state = world.state()
    row = next((r for r in state.get("clinic_fault_injections", []) if r.get("fault_id") == 'FI-401'), None)
    if not row or row.get("is_active") != 1:
        return "clinic_fault_injections FI-401 is missing or its is_active is not 1"
    return None
