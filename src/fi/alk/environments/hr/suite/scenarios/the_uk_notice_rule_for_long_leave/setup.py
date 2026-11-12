def setup(world):
    world.put("employees", {
        "employee_id": "E457",
        "name": "Gillian Mercer",
        "email": "gillian.mercer@example.com",
        "region": "UK",
        "manager_id": "M02",
        "role": "software_engineer",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E457-ANN",
        "employee_id": "E457",
        "leave_type": "annual",
        "allocated_days": 25,
        "used_days": 2,
        "reserved_days": 0,
        "available_days": 23,
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E457"}, by="id")
