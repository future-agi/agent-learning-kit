def handle(args, db):
    import json
    import re
    from datetime import datetime, timedelta

    attendees = args.get("attendeeEmails")
    if not attendees or not isinstance(attendees, list) or not all(isinstance(a, str) and "@" in a for a in attendees):
        raise ToolError("attendeeEmails must be a non-empty list of email addresses", category="invalid_input")
    start_str = args.get("startTime")
    end_str = args.get("endTime")
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    if not start_str or not end_str or not re.match(iso_pattern, start_str) or not re.match(iso_pattern, end_str):
        raise ToolError("startTime and endTime must be ISO 8601 timestamps", category="invalid_input")
    duration = args.get("durationMinutes")
    if duration is None:
        duration = 30
    if not isinstance(duration, int) or duration <= 0:
        raise ToolError("durationMinutes must be a positive integer", category="invalid_input")
    preferences = args.get("preferences") or {}
    if not isinstance(preferences, dict):
        raise ToolError("preferences must be an object", category="invalid_input")
    page_size = preferences.get("pageSize")
    if page_size is None:
        page_size = 5
    if not isinstance(page_size, int) or page_size < 1:
        raise ToolError("preferences.pageSize must be a positive integer", category="invalid_input")
    tz_name = args.get("timeZone")

    def parse_iso(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    t_min, t_max = parse_iso(start_str), parse_iso(end_str)
    if t_min >= t_max:
        raise ToolError("startTime must be earlier than endTime", category="invalid_input")

    tzinfo = None
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            tzinfo = ZoneInfo(tz_name)
        except Exception:
            raise ToolError(f"timeZone must be an IANA time zone name, not {tz_name!r}", category="invalid_input")
    else:
        tz_name = "UTC"

    def hour_minute(name):
        value = preferences.get(name)
        if value is None:
            return None
        if not isinstance(value, str) or not re.match(r"^\d{2}:\d{2}$", value):
            raise ToolError(f"preferences.{name} must be HH:mm", category="invalid_input")
        return value

    start_hour, end_hour = hour_minute("startHour"), hour_minute("endHour")
    exclude_weekends = bool(preferences.get("excludeWeekends"))

    buf_row = db.one("SELECT value FROM environment_policy WHERE name = 'buffer_minutes'")
    buffer_min = int(json.loads(buf_row["value"])) if buf_row else 10

    allowlist_row = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_domains'")
    allowed = json.loads(allowlist_row["value"]) if allowlist_row else "*"
    if allowed != "*":
        for email in attendees:
            if email.split("@")[-1] not in allowed:
                raise ToolError(f"Policy violation: attendee domain {email.split('@')[-1]!r} is not in the attendee allowlist.", category="policy_blocked")

    busy = []
    for email in attendees:
        for er in db.query(
            "SELECT e.start_time, e.end_time FROM events e JOIN event_attendees a ON e.event_id = a.event_id "
            "WHERE a.email = ? AND e.status != 'cancelled' AND e.end_time > ? AND e.start_time < ?",
            (email, start_str, end_str),
        ):
            busy.append((parse_iso(er["start_time"]) - timedelta(minutes=buffer_min), parse_iso(er["end_time"]) + timedelta(minutes=buffer_min)))

    step, dur = timedelta(minutes=30), timedelta(minutes=duration)
    curr = t_min
    if curr.minute % 30 != 0:
        curr = curr.replace(second=0, microsecond=0) + timedelta(minutes=30 - curr.minute % 30)
    slots = []
    while curr + dur <= t_max and len(slots) < page_size:
        slot_start, slot_end = curr, curr + dur
        local_start = slot_start.astimezone(tzinfo) if tzinfo else slot_start
        local_end = slot_end.astimezone(tzinfo) if tzinfo else slot_end
        inside_hours = (not start_hour or local_start.strftime("%H:%M") >= start_hour) and (not end_hour or local_end.strftime("%H:%M") <= end_hour)
        weekday_ok = not exclude_weekends or local_start.weekday() < 5
        if inside_hours and weekday_ok and not any(max(slot_start, bs) < min(slot_end, be) for bs, be in busy):
            slots.append({
                "start": {"dateTime": slot_start.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": tz_name},
                "end": {"dateTime": slot_end.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": tz_name},
            })
        curr += step
    return {"timeSlots": slots}
