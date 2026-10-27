def handle(args, db):
    import json

    cal_id = args.get("calendar_id")
    evt_id = args.get("event_id")

    if not cal_id or not evt_id:
        raise ToolError("Missing required parameters: calendar_id and event_id are required.")

    cal = db.one("SELECT * FROM calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}")
    if cal["access_role"] not in ("writer", "owner"):
        raise ToolError(f"Permission denied: calendar {cal_id!r} is read-only (access_role: {cal['access_role']}).")

    evt = db.one("SELECT * FROM events WHERE calendar_id = ? AND event_id = ?", (cal_id, evt_id))
    if not evt:
        raise ToolError(f"Event {evt_id!r} not found on calendar {cal_id!r}.")
    if evt["status"] == "cancelled":
        raise ToolError(f"Event {evt_id!r} is already cancelled.")

    req_app_row = db.one("SELECT value FROM environment_policy WHERE name = 'require_approval_for_mutations'")
    require_app = True
    if req_app_row:
        try:
            require_app = bool(json.loads(req_app_row["value"]))
        except Exception:
            require_app = True

    approval_id = args.get("approval_id")
    if require_app:
        if not approval_id:
            raise ToolError("Policy error: approval_id is required to delete an event.")
        app = db.one("SELECT * FROM approvals WHERE approval_id = ? AND status = 'approved'", (approval_id,))
        if not app:
            raise ToolError(f"Invalid or unapproved approval_id: {approval_id!r}.")

    recurring_action = args.get("recurring_action", "this_event")
    clock_row = db.one("SELECT now FROM clock LIMIT 1")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"

    if recurring_action == "all_events" and evt["recurrence_rule"]:
        db.execute("UPDATE events SET status = 'cancelled', updated_at = ? WHERE recurring_event_id = ? OR event_id = ?", (now_str, evt_id, evt_id))
    else:
        db.execute("UPDATE events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", (now_str, evt_id))

    msg_id = f"MSG-DEL-{evt_id}"
    db.execute(
        "INSERT INTO simulated_outbox (message_id, event_id, recipient_email, action, subject, payload, sent_at) "
        "VALUES (?, ?, 'all-attendees', 'cancellation', ?, ?, ?)",
        (msg_id, evt_id, f"Cancelled: {evt['summary']}", json.dumps({"status": "cancelled"}), now_str)
    )

    return {
        "calendarId": cal_id,
        "deleted": True,
        "eventId": evt_id,
        "recurringAction": recurring_action,
        "status": "cancelled"
    }