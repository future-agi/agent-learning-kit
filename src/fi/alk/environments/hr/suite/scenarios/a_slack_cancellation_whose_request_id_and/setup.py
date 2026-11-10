def setup(world):
    world.put("employees", {
        "employee_id": "E453",
        "name": "Cedric Holloway",
        "email": "cedric.holloway@example.com",
        "region": "US",
        "manager_id": "M01",
        "role": "software_engineer",
    })
    world.put("slack_users", {
        "user_id": "U_E453",
        "name": "Cedric Holloway",
        "email": "cedric.holloway@example.com",
        "role": "software_engineer",
    })
    world.put("slack_channel_members", {
        "id": "SCM453",
        "channel_id": "CH_HRHELP",
        "user_id": "U_E453",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E453-ANN",
        "employee_id": "E453",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 0,
        "reserved_days": 4,
        "available_days": 16,
    })
    world.put("leave_requests", {
        "request_id": "REQ-4053",
        "employee_id": "E453",
        "leave_type": "annual",
        "start_date": "2026-12-10",
        "end_date": "2026-12-11",
        "working_days": 2,
        "status": "pending",
        "reason": "Conference trip part 2",
        "approver_id": "M01",
        "created_at": "2026-10-21T09:00:00Z",
        "decided_at": None,
        "decided_by": None,
        "updated_at": None,
    })
    world.put("leave_requests", {
        "request_id": "REQ-4054",
        "employee_id": "E453",
        "leave_type": "annual",
        "start_date": "2026-12-03",
        "end_date": "2026-12-04",
        "working_days": 2,
        "status": "pending",
        "reason": "Conference trip part 1",
        "approver_id": "M01",
        "created_at": "2026-10-21T09:00:00Z",
        "decided_at": None,
        "decided_by": None,
        "updated_at": None,
    })
    world.put("slack_messages", {
        "message_id": "SM450",
        "channel_id": "CH_HRHELP",
        "user_id": "U_E453",
        "text": "Please cancel REQ-4053 for 3-4 Dec, conference moved.",
        "ts": "1792600000.000450",
        "thread_ts": None,
        "permalink": "https://acme.slack.example.com/archives/CH_HRHELP/p1792600000000450",
    })
