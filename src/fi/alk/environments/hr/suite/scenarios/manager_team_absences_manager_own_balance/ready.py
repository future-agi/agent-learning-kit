def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b.get("employee_id") == "M03" and b.get("leave_type") == "annual"), None)
    if not bal:
        return "Balance for M03 annual missing"
    return None
