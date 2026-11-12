def setup(world):
    world.put("employees", {"employee_id": "E404", "name": "Elena Vasquez", "region": "US", "manager_id": "M01", "email": "elena.vasquez@example.com", "role": "developer"})
    world.put("leave_balances", {"balance_id": "BAL-E404-ANN", "employee_id": "E404", "leave_type": "annual", "allocated_days": 20, "used_days": 10, "reserved_days": 0, "available_days": 10})
    world.put("leave_balances", {"balance_id": "BAL-E404-SCK", "employee_id": "E404", "leave_type": "sick", "allocated_days": 10, "used_days": 3, "reserved_days": 0, "available_days": 7})
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E404"}, by="id")
