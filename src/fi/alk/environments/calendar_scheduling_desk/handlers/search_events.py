def handle(args, db):
    import re
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    
    q = args.get("query")
    cal_id = args.get("calendar_id")
    attendee = args.get("attendee_email")
    time_min = args.get("time_min")
    time_max = args.get("time_max")
    max_results = args.get("max_results", 20)
    
    if time_min and not re.match(iso_pattern, time_min):
        raise ToolError(f"Invalid ISO format for time_min: {time_min}")
    if time_max and not re.match(iso_pattern, time_max):
        raise ToolError(f"Invalid ISO format for time_max: {time_max}")
        
    sql = "SELECT DISTINCT e.* FROM events e LEFT JOIN event_attendees a ON e.event_id = a.event_id WHERE e.status != 'cancelled'"
    params = []
    
    if cal_id:
        sql += " AND e.calendar_id = ?"
        params.append(cal_id)
    if attendee:
        sql += " AND (a.email = ? OR a.email LIKE ?)"
        params.extend([attendee, f"%{attendee}%"])
    if q:
        sql += " AND (e.summary LIKE ? OR e.description LIKE ? OR e.location LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if time_min:
        sql += " AND e.end_time >= ?"
        params.append(time_min)
    if time_max:
        sql += " AND e.start_time <= ?"
        params.append(time_max)
        
    sql += " ORDER BY e.start_time ASC LIMIT ?"
    params.append(max_results)
    
    rows = db.query(sql, params)
    res = []
    for r in rows:
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
        res.append({
            "id": r["event_id"],
            "calendarId": r["calendar_id"],
            "summary": r["summary"],
            "description": r["description"],
            "location": r["location"],
            "start": {"dateTime": r["start_time"], "timeZone": r["time_zone"]},
            "end": {"dateTime": r["end_time"], "timeZone": r["time_zone"]},
            "status": r["status"],
            "attendees": attendees,
            "version": r["version"]
        })
    return {"items": res}