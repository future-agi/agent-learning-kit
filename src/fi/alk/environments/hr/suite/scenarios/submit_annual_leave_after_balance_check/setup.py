def setup(world):
    world.put("employees", {
        "employee_id": "E410",
        "name": "Julian Hayes",
        "email": "julian.hayes@example.com",
        "region": "US",
        "manager_id": "M01",
        "role": "software_engineer"
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E410-ANN",
        "employee_id": "E410",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 5,
        "reserved_days": 0,
        "available_days": 15
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E410-SCK",
        "employee_id": "E410",
        "leave_type": "sick",
        "allocated_days": 10,
        "used_days": 0,
        "reserved_days": 0,
        "available_days": 10
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E410"}, by="id")
