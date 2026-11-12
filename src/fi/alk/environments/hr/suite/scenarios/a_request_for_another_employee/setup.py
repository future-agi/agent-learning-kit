def setup(world):
    world.put("employees", {
        "employee_id": "E455",
        "name": "Valerie Ramos",
        "email": "valerie.ramos@example.com",
        "region": "US",
        "manager_id": "M01",
        "role": "data_analyst",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E455-ANN",
        "employee_id": "E455",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 2,
        "reserved_days": 0,
        "available_days": 18,
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E455"}, by="id")
