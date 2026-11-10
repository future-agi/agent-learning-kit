def setup(world):
    world.put("employees", {"employee_id": "E401", "name": "Sarah Thornton", "region": "US", "manager_id": "M01", "email": "sarah.thornton@example.com", "role": "engineer"})
    world.put("leave_balances", {"balance_id": "BAL-E401-ANN", "employee_id": "E401", "leave_type": "annual", "allocated_days": 20, "used_days": 5, "reserved_days": 0, "available_days": 15})
    world.put("leave_balances", {"balance_id": "BAL-E401-SCK", "employee_id": "E401", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
