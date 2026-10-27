def handle(args, db):
    import json
    import re
    from datetime import datetime, timedelta

    attendees = args.get("attendees")
    if not attendees or not isinstance(attendees, list):
        raise ToolError("Missing or invalid required parameter: attendees (must be a non-empty list of emails)")
        
    duration = args.get("duration_minutes")
    if not duration or duration <= 0:
        raise ToolError("Missing or invalid required parameter: duration_minutes (must be positive integer)")
        
    time_min_str = args.get("time_min")
    time_max_str = args.get("time_max")
    if not time_min_str or not time_max_str:
        raise ToolError("Missing required parameters: time_min and time_max")
        
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    if not re.match(iso_pattern, time_min_str) or not re.match(iso_pattern, time_max_str):
        raise ToolError("time_min and time_max must be valid ISO-8601 timestamps")

    def parse_iso(s):
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)

    try:
        t_min = parse_iso(time_min_str)
        t_max = parse_iso(time_max_str)
    except Exception as e:
        raise ToolError(f"Could not parse timestamps: {e}")
        
    if t_min >= t_max:
        raise ToolError("time_min must be earlier than time_max")

    max_candidates = args.get("max_candidates", 5)
    tz_str = args.get("time_zone", "UTC")

    buf_row = db.one("SELECT value FROM environment_policy WHERE name = 'buffer_minutes'")
    buffer_min = 10
    if buf_row:
        try:
            buffer_min = int(json.loads(buf_row["value"]))
        except Exception:
            buffer_min = 10

    busy_intervals = []
    for email in attendees:
        evt_rows = db.query(
            "SELECT e.start_time, e.end_time FROM events e "
            "JOIN event_attendees a ON e.event_id = a.event_id "
            "WHERE a.email = ? AND e.status != 'cancelled' AND e.end_time > ? AND e.start_time < ?",
            (email, time_min_str, time_max_str)
        )
        for er in evt_rows:
            try:
                es = parse_iso(er["start_time"]) - timedelta(minutes=buffer_min)
                ee = parse_iso(er["end_time"]) + timedelta(minutes=buffer_min)
                busy_intervals.append((es, ee))
            except Exception:
                pass

    step = timedelta(minutes=30)
    dur = timedelta(minutes=duration)
    curr = t_min
    candidates = []

    minute = curr.minute
    if minute % 30 != 0:
        add_m = (30 - (minute % 30))
        curr = curr.replace(second=0, microsecond=0) + timedelta(minutes=add_m)

    while curr + dur <= t_max and len(candidates) < max_candidates:
        slot_start = curr
        slot_end = curr + dur
        
        conflict = False
        for bs, be in busy_intervals:
            if max(slot_start, bs) < min(slot_end, be):
                conflict = True
                break
                
        if not conflict:
            candidates.append({
                "start": slot_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": slot_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeZone": tz_str,
                "attendees": attendees,
                "durationMinutes": duration
            })
        curr += step

    return {
        "candidates": candidates,
        "searchWindow": {
            "start": time_min_str,
            "end": time_max_str
        },
        "bufferMinutesApplied": buffer_min
    }