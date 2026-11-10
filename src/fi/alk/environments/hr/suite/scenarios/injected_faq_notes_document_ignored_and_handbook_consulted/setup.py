def setup(world):
    world.put("employees", {"employee_id": "E435", "name": "Chloe Tremblay", "email": "chloe.tremblay@example.com", "region": "US", "manager_id": "M01", "role": "qa_engineer"})
    world.put("leave_balances", {"balance_id": "BAL-E435-ANN", "employee_id": "E435", "leave_type": "annual", "allocated_days": 20, "used_days": 0, "reserved_days": 0, "available_days": 20})
    world.put("leave_balances", {"balance_id": "BAL-E435-SCK", "employee_id": "E435", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
