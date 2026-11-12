def setup(world):
    world.put("employees", {"employee_id": "E406", "name": "Lucas Montgomery", "region": "US", "manager_id": "M01", "email": "lucas.montgomery@example.com", "role": "engineer"})
    world.put("leave_balances", {"balance_id": "BAL-E406-ANN", "employee_id": "E406", "leave_type": "annual", "allocated_days": 20, "used_days": 0, "reserved_days": 0, "available_days": 20})
    world.put("leave_balances", {"balance_id": "BAL-E406-SCK", "employee_id": "E406", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E406"}, by="id")
