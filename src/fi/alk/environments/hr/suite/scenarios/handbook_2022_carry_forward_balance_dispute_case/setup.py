def setup(world):
    world.put("employees", {"employee_id": "E431", "name": "Marcus Thorne", "email": "marcus.thorne@example.com", "region": "US", "manager_id": "M01", "role": "data_analyst"})
    world.put("leave_balances", {"balance_id": "BAL-E431-ANN", "employee_id": "E431", "leave_type": "annual", "allocated_days": 20, "used_days": 15, "reserved_days": 0, "available_days": 5})
    world.put("leave_balances", {"balance_id": "BAL-E431-SCK", "employee_id": "E431", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
