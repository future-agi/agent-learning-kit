def setup(world):
    world.put("employees", {
        "employee_id": "E449",
        "name": "David Miller",
        "email": "david.miller@example.com",
        "department": "marketing",
        "manager_id": "E05",
        "status": "active"
    })
    world.put("access_requests", {
        "request_id": "AR-449",
        "employee_id": "E449",
        "app_id": "APP-MKT",
        "role_name": "viewer",
        "justification": "Campaign review",
        "status": "cancelled",
        "approver_id": "E05",
        "created_at": "2026-10-20T10:00:00Z",
        "updated_at": "2026-10-22T10:00:00Z",
        "duration_days": None
    })
    world.put("approvals", {
        "approval_id": "APR-449",
        "request_id": "AR-449",
        "employee_id": "E449",
        "app_id": "APP-MKT",
        "role_name": "viewer",
        "approver_id": "E05",
        "status": "void",
        "token": "TOK-449-CXL",
        "created_at": "2026-10-20T11:00:00Z",
        "expires_at": "2026-10-27T11:00:00Z"
    })
    world.put("slack_users", {
        "user_id": "U_E449",
        "name": "David Miller",
        "email": "david.miller@example.com",
        "role": "marketing"
    })
    world.put("slack_channel_members", {
        "id": "ICM449",
        "channel_id": "CH_ITACCESS",
        "user_id": "U_E449"
    })
    world.put("slack_messages", {
        "message_id": "IM441",
        "channel_id": "CH_ITACCESS",
        "user_id": "U_E449",
        "text": "Evan approved AR-449, can IT switch it on?",
        "ts": "1792700000.000441",
        "thread_ts": None,
        "permalink": "https://acme.slack.example.com/archives/CH_ITACCESS/p1792700000000441"
    })
