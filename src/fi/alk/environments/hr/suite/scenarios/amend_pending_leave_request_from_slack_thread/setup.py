def setup(world):
    world.put("employees", {
        "employee_id": "E423",
        "name": "Harrison Wells",
        "email": "harrison.wells@example.com",
        "region": "US",
        "manager_id": "M01",
        "role": "software_engineer"
    })
    world.put("slack_users", {
        "user_id": "U_E423",
        "name": "Harrison Wells",
        "email": "harrison.wells@example.com",
        "role": "software_engineer"
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E423-ANN",
        "employee_id": "E423",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 3,
        "reserved_days": 3,
        "available_days": 14
    })
    world.put("leave_requests", {
        "request_id": "REQ-4103",
        "employee_id": "E423",
        "leave_type": "annual",
        "start_date": "2026-12-21",
        "end_date": "2026-12-23",
        "working_days": 3,
        "status": "pending",
        "approver_id": "M01",
        "reason": "Christmas travel",
        "created_at": "2026-10-21T14:00:00Z",
        "updated_at": None,
        "decided_by": None,
        "decided_at": None
    })
    world.put("slack_messages", {
        "message_id": "SM423",
        "channel_id": "CH_HRHELP",
        "user_id": "U_E423",
        "text": "Can I take 21 to 23 December? I put in REQ-4103.",
        "ts": "1792656000.000423",
        "thread_ts": None,
        "permalink": "https://acme.slack.example.com/archives/CH_HRHELP/p1792656000000423"
    })
    world.put("slack_messages", {
        "message_id": "SM424",
        "channel_id": "CH_HRHELP",
        "user_id": "U_E423",
        "text": "Sorry, correction: I need 22 to 24 December instead, not 21 to 23. Can someone update REQ-4103?",
        "ts": "1792658400.000424",
        "thread_ts": "1792656000.000423",
        "permalink": "https://acme.slack.example.com/archives/CH_HRHELP/p1792658400000424"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    # The thread holds the correction among other replies; an older plan and an unrelated question of
    # Harrison's sit beside it, and Charlie's base thread asks about the same days.
    for message_id, user, text, ts, thread in (
        ("SM425", "U_E423", "Context: my sister's wedding is on the 23rd, so I'm travelling the day before.", "1792656900.000425", "1792656000.000423"),
        ("SM426", "U_M01", "Fine by me once it's updated in the system, Harrison.", "1792659900.000426", "1792656000.000423"),
        ("SM427", "U_E423", "And just to be sure: 22nd to 24th, all three days. Thanks!", "1792660800.000427", "1792656000.000423"),
        ("SM428", "U_E423", "Thinking of taking 14 to 16 December off, does anyone know if that week is busy?", "1792058400.000428", None),
        ("SM429", "U_E423", "Does anyone know if the parking garage is closed over Christmas?", "1792497600.000429", None),
    ):
        world.put("slack_messages", {
            "message_id": message_id,
            "channel_id": "CH_HRHELP",
            "user_id": user,
            "text": text,
            "ts": ts,
            "thread_ts": thread,
            "permalink": "https://acme.slack.example.com/archives/CH_HRHELP/p" + ts.replace(".", ""),
        })
    world.change("session_caller", "default", {"employee_id": "E423"}, by="id")
