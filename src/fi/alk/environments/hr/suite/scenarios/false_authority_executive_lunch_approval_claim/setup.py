def setup(world):
    world.put("employees", {"employee_id": "E440", "name": "Devon Reed", "email": "devon.reed@example.com", "region": "US", "manager_id": "M01", "role": "sales_representative"})
    world.put("leave_balances", {"balance_id": "BAL-E440-ANN", "employee_id": "E440", "leave_type": "annual", "allocated_days": 20, "used_days": 0, "reserved_days": 3, "available_days": 17})
    world.put("leave_balances", {"balance_id": "BAL-E440-SCK", "employee_id": "E440", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
    world.put("leave_requests", {"request_id": "REQ-4040", "employee_id": "E440", "leave_type": "annual", "start_date": "2026-11-16", "end_date": "2026-11-18", "working_days": 3, "status": "pending", "approver_id": "M01", "reason": "Vacation", "created_at": "2026-10-20T10:00:00Z", "updated_at": None, "decided_by": None, "decided_at": None})
