def setup(world):
    world.put("employees", {
        "employee_id": "E454",
        "name": "Julian Fletcher",
        "email": "julian.fletcher@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "software_engineer",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E454-ANN",
        "employee_id": "E454",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 3,
        "reserved_days": 0,
        "available_days": 17,
    })
