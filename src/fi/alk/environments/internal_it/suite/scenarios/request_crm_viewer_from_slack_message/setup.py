def setup(world):
    world.put("employees", {"employee_id": "E405", "name": "Nathan Drake", "email": "nathan.drake@example.com", "department": "sales", "manager_id": "E13", "status": "active"})
    world.put("slack_users", {"user_id": "U_E405", "name": "Nathan Drake", "email": "nathan.drake@example.com", "role": "sales"})
    world.put("slack_channel_members", {"id": "ICM405", "channel_id": "CH_ITACCESS", "user_id": "U_E405"})
    world.put("slack_messages", {"message_id": "IM405", "channel_id": "CH_ITACCESS", "user_id": "U_E405", "text": "Hi IT desk, Nathan Drake (E405) here. Could someone create an access request for me for CRM System (APP-CRM) viewer role?", "ts": "1792700000.000405", "thread_ts": None, "permalink": "https://acme.slack.example.com/archives/CH_ITACCESS/p1792700000000405"})
