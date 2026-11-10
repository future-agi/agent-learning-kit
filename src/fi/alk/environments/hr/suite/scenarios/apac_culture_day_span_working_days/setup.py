def setup(world):
    world.put("employees", {"employee_id": "E403", "name": "Haruto Tanaka", "region": "APAC", "manager_id": "M04", "email": "haruto.tanaka@example.com", "role": "engineer"})
    world.put("leave_balances", {"balance_id": "BAL-E403-ANN", "employee_id": "E403", "leave_type": "annual", "allocated_days": 18, "used_days": 4, "reserved_days": 0, "available_days": 14})
    world.put("leave_balances", {"balance_id": "BAL-E403-SCK", "employee_id": "E403", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
