def setup(world):
    world.put("employees", {
        "employee_id": "E458",
        "name": "Patrick Stewart",
        "email": "patrick.stewart@example.com",
        "region": "UK",
        "manager_id": "M02",
        "role": "accountant",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E458-ANN",
        "employee_id": "E458",
        "leave_type": "annual",
        "allocated_days": 25,
        "used_days": 25,
        "reserved_days": 0,
        "available_days": 0,
    })
    world.put("hr_cases", {
        "case_id": "CASE-4050",
        "employee_id": "E458",
        "category": "balance_dispute",
        "status": "in_review",
        "subject": "Missing 3 carry-forward days",
        "description": "My 2025 carry-forward of 3 days was not added.",
        "notes": "HR02: Investigating payroll export.",
        "created_at": "2026-10-20T10:00:00Z",
        "updated_at": "2026-10-20T10:00:00Z",
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E458"}, by="id")
