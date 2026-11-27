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
        "allocated_days": 18,
        "used_days": 1,
        "reserved_days": 0,
        "available_days": 17,
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E454"}, by="id")
