def setup(world):
    world.put("employees", {
        "employee_id": "E422",
        "name": "Maya Patel",
        "email": "maya.patel@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "data_analyst"
    })
    world.put("slack_users", {
        "user_id": "U_E422",
        "name": "Maya Patel",
        "email": "maya.patel@example.com",
        "role": "data_analyst"
    })
    world.put("slack_channel_members", {
        "id": "SCM-4022",
        "channel_id": "CH_HRHELP",
        "user_id": "U_E422"
    })
    world.put("leave_requests", {
        "request_id": "REQ-4022",
        "employee_id": "E422",
        "leave_type": "annual",
        "start_date": "2026-12-01",
        "end_date": "2026-12-04",
        "working_days": 4,
        "status": "pending",
        "approver_id": "M04",
        "reason": "Conference",
        "created_at": "2026-10-22T10:00:00Z",
        "updated_at": None,
        "decided_by": None,
        "decided_at": None
    })
    world.put("slack_messages", {
        "message_id": "SM-4022",
        "channel_id": "CH_HRHELP",
        "user_id": "U_E422",
        "text": "Hi HR desk, has my annual leave request REQ-4022 been approved yet?",
        "ts": "1792680000.000010",
        "thread_ts": None,
        "permalink": "https://acme.slack.example.com/archives/CH_HRHELP/p1792680000000010"
    })
    import hashlib
    approved_text = "Hi Maya, your leave request REQ-4022 is currently pending manager approval from M04."
    chash = hashlib.sha256(approved_text.encode("utf-8")).hexdigest()
    world.put("slack_approvals", {
        "approval_id": "APP-4022",
        "actor_id": "U_HRDESK",
        "channel_id": "CH_HRHELP",
        "content_hash": chash,
        "approved_text": approved_text,
        "status": "approved",
        "expires_at": "2026-10-30T00:00:00Z"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E422"}, by="id")
