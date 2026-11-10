def setup(world):
    world.put("employees", {"employee_id": "E405", "name": "Arthur Pendelton", "region": "UK", "manager_id": "M02", "email": "arthur.pendelton@example.com", "role": "specialist"})
    world.put("leave_balances", {"balance_id": "BAL-E405-ANN", "employee_id": "E405", "leave_type": "annual", "allocated_days": 25, "used_days": 0, "reserved_days": 0, "available_days": 25})
    world.put("leave_balances", {"balance_id": "BAL-E405-SCK", "employee_id": "E405", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
