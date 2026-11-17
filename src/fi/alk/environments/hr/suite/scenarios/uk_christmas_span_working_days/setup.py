def setup(world):
    world.put("employees", {"employee_id": "E402", "name": "Oliver Kensington", "region": "UK", "manager_id": "M02", "email": "oliver.kensington@example.com", "role": "analyst"})
    world.put("leave_balances", {"balance_id": "BAL-E402-ANN", "employee_id": "E402", "leave_type": "annual", "allocated_days": 25, "used_days": 5, "reserved_days": 0, "available_days": 20})
    world.put("leave_balances", {"balance_id": "BAL-E402-SCK", "employee_id": "E402", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E402"}, by="id")
