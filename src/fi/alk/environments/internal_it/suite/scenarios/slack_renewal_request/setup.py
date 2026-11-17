def setup(world):
    world.put("employees", {"employee_id": "E412", "name": "Ravi Menon", "email": "ravi.menon@example.com", "department": "engineering", "manager_id": "E05", "status": "active"})
    world.put("slack_users", {"user_id": "U_E412", "name": "Ravi Menon", "email": "ravi.menon@example.com", "role": "engineering"})
    world.put("slack_messages", {"message_id": "IM412", "channel_id": "CH_ITOPS", "user_id": "U_E412", "text": "My deployer access on APP-PROD expired this morning; could IT raise a renewal request for me?", "ts": "1792933200.000412", "thread_ts": None, "permalink": "https://acme.slack.example.com/archives/CH_ITOPS/p1792933200000412"})
    world.put("entitlements", {"entitlement_id": "ENT-412", "employee_id": "E412", "app_id": "APP-PROD", "role_name": "deployer", "status": "expired", "granted_at": "2026-06-01T09:00:00Z", "expires_at": "2026-10-25T09:00:00Z", "granted_by": "E05"})
    world.put("employees", {"employee_id": "E516", "name": "Rosa Delgado", "email": "rosa.delgado@example.com", "department": "it_operations", "manager_id": "E21", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E516"}, by="id")
