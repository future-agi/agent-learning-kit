def setup(world):
    world.put("employees", {
        "employee_id": "E418",
        "name": "Pooja Sharma",
        "email": "pooja.sharma@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "software_engineer"
    })
    world.put("hr_cases", {
        "case_id": "CASE-4018",
        "employee_id": "E418",
        "category": "balance_dispute",
        "subject": "Missing carry forward days from 2025",
        "description": "5 days of carry-forward leave not credited to 2026 balance",
        "status": "in_review",
        "notes": "HR03: reviewing payroll records from Dec 2025",
        "created_at": "2026-10-18T10:00:00Z",
        "updated_at": "2026-10-20T12:00:00Z"
    })
