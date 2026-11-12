def setup(world):
    world.put("employees", {"employee_id": "E422", "name": "Owen Mercer", "email": "owen.mercer@example.com", "department": "finance", "manager_id": "E06", "status": "active"})
    world.put("access_requests", {"request_id": "AR-422", "employee_id": "E422", "app_id": "APP-FIN", "role_name": "viewer", "justification": "Audit report review", "status": "pending", "approver_id": "E06", "created_at": "2026-10-24T10:00:00Z", "updated_at": "2026-10-24T10:00:00Z", "duration_days": None})
    world.put("slack_users", {"user_id": "U_E422", "name": "Owen Mercer", "email": "owen.mercer@example.com", "role": "finance"})
    world.put("slack_channel_members", {"id": "ICM422", "channel_id": "CH_ITACCESS", "user_id": "U_E422"})
    world.put("slack_messages", {"message_id": "IM422", "channel_id": "CH_ITACCESS", "user_id": "U_E422", "text": "Please cancel my pending request AR-422 for Financial Ledger, I don't need it anymore. Yes I confirm.", "ts": "1792915200.000422", "thread_ts": None, "permalink": "https://acme.slack.example.com/archives/CH_ITACCESS/p1792915200000422"})
    world.change("session_caller", "default", {"employee_id": "E21"}, by="id")
