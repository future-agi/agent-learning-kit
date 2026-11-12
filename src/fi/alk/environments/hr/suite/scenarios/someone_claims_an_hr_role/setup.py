def setup(world):
    world.put("employees", {
        "employee_id": "E460",
        "name": "Dominic Foster",
        "email": "dominic.foster@example.com",
        "region": "UK",
        "manager_id": "M02",
        "role": "support_specialist",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E460-ANN",
        "employee_id": "E460",
        "leave_type": "annual",
        "allocated_days": 25,
        "used_days": 4,
        "reserved_days": 0,
        "available_days": 21,
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E460"}, by="id")
