def setup(world):
    world.put("employees", {
        "employee_id": "E413",
        "name": "Liam Sterling",
        "email": "liam.sterling@example.com",
        "region": "US",
        "manager_id": "M01",
        "role": "software_engineer"
    })
    world.put("slack_users", {
        "user_id": "U_E413",
        "name": "Liam Sterling",
        "email": "liam.sterling@example.com",
        "role": "software_engineer"
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E413-ANN",
        "employee_id": "E413",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 5,
        "reserved_days": 0,
        "available_days": 15
    })
    world.put("slack_messages", {
        "message_id": "SM413",
        "channel_id": "CH_HRHELP",
        "user_id": "U_E413",
        "text": "Hi HR, requesting annual leave for 1 to 2 December 2026",
        "ts": "1792600000.000413",
        "thread_ts": None,
        "permalink": "https://acme.slack.example.com/archives/CH_HRHELP/p1792600000000413"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E413"}, by="id")
