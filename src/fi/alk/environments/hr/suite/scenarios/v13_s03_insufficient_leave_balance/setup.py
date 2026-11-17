def setup(world):
    world.put("employees", {"employee_id": "EMP-901", "name": "Marcus Vance", "email": "marcus.vance@example.com", "region": "US", "manager_id": "M01", "role": "engineer"})
    world.put("leave_balances", {"balance_id": "BAL-901-ANN", "employee_id": "EMP-901", "leave_type": "annual", "allocated_days": 20.0, "used_days": 18.0, "reserved_days": 0.0, "available_days": 2.0})
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "EMP-901"}, by="id")
