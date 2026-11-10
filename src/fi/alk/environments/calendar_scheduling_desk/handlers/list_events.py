def handle(args, db):
    import re
    cal_id = args.get("calendar_id")
    if not cal_id:
        raise ToolError("Missing required parameter: calendar_id")
    
    cal = db.one("SELECT * FROM calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}")
    
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    
    time_min = args.get("time_min")
    if time_min:
        if not re.match(iso_pattern, time_min):
            raise ToolError(f"Invalid ISO format for time_min: {time_min}")
            
    time_max = args.get("time_max")
    if time_max:
        if not re.match(iso_pattern, time_max):
            raise ToolError(f"Invalid ISO format for time_max: {time_max}")
            
    max_results = args.get("max_results", 50)
    page_token = args.get("page_token")
    
    offset = 0
    if page_token:
        try:
            offset = int(page_token)
        except Exception:
            raise ToolError(f"Invalid page_token: {page_token}")
            
    query = "SELECT * FROM events WHERE calendar_id = ? AND status != 'cancelled'"
    params = [cal_id]
    
    if time_min:
        query += " AND end_time >= ?"
        params.append(time_min)
    if time_max:
        query += " AND start_time <= ?"
        params.append(time_max)
        
    query += " ORDER BY start_time ASC LIMIT ? OFFSET ?"
    params.extend([max_results + 1, offset])
    
    rows = db.query(query, params)
    
    has_more = len(rows) > max_results
    if has_more:
        items_rows = rows[:max_results]
        next_token = str(offset + max_results)
    else:
        items_rows = rows
        next_token = None
        
    events = []
    for r in items_rows:
        att_rows = db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM event_attendees WHERE event_id = ?", (r["event_id"],))
        attendees = [
            {
                "email": a["email"],
                "displayName": a["display_name"],
                "responseStatus": a["response_status"],
                "organizer": bool(a["is_organizer"]),
                "optional": bool(a["is_optional"])
            }
            for a in att_rows
        ]
        events.append({
            "id": r["event_id"],
            "summary": r["summary"],
            "description": r["description"],
            "location": r["location"],
            "start": {"dateTime": r["start_time"], "timeZone": r["time_zone"]},
            "end": {"dateTime": r["end_time"], "timeZone": r["time_zone"]},
            "status": r["status"],
            "attendees": attendees,
            "version": r["version"],
            "recurringEventId": r["recurring_event_id"],
            "recurrence": [r["recurrence_rule"]] if r["recurrence_rule"] else []
        })
        
    return {
        "items": events,
        "nextPageToken": next_token
    }