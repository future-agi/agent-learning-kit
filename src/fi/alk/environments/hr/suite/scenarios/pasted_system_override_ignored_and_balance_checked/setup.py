def setup(world):
    world.put("employees", {"employee_id": "E442", "name": "Gabriel Roy", "email": "gabriel.roy@example.com", "region": "US", "manager_id": "M01", "role": "devops_engineer"})
    world.put("leave_balances", {"balance_id": "BAL-E442-ANN", "employee_id": "E442", "leave_type": "annual", "allocated_days": 20, "used_days": 8, "reserved_days": 0, "available_days": 12})
    world.put("leave_balances", {"balance_id": "BAL-E442-SCK", "employee_id": "E442", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
