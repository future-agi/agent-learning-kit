def setup(world):
    world.put("employees", {"employee_id": "E436", "name": "Ananya Patel", "email": "ananya.patel@example.com", "region": "APAC", "manager_id": "M04", "role": "frontend_developer"})
    world.put("leave_balances", {"balance_id": "BAL-E436-ANN", "employee_id": "E436", "leave_type": "annual", "allocated_days": 18, "used_days": 0, "reserved_days": 0, "available_days": 18})
    world.put("leave_balances", {"balance_id": "BAL-E436-SCK", "employee_id": "E436", "leave_type": "sick", "allocated_days": 12, "used_days": 0, "reserved_days": 0, "available_days": 12})
