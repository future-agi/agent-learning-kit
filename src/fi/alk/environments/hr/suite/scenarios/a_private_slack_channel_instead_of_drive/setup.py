def setup(world):
    world.put("employees", {
        "employee_id": "E459",
        "name": "Lucas Campbell",
        "email": "lucas.campbell@example.com",
        "region": "US",
        "manager_id": "M01",
        "role": "software_engineer",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E459-ANN",
        "employee_id": "E459",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 5,
        "reserved_days": 0,
        "available_days": 15,
    })
