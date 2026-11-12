def handle(args, db):
    page_size = args.get("pageSize")
    # The reps' calendars are internal: which calendars exist and who owns them is staff knowledge.
    if _caller(db)[0] != "staff":
        raise ToolError("Permission denied: the reps' calendars are internal; only BrightDesk staff in this conversation can list them.", category="permission_denied")
    if page_size is None:
        page_size = 100
    if not isinstance(page_size, int) or page_size < 1 or page_size > 250:
        raise ToolError("pageSize must be an integer from 1 to 250", category="invalid_input")
    offset = 0
    if args.get("pageToken"):
        try:
            offset = int(args["pageToken"])
        except ValueError:
            raise ToolError(f"Invalid pageToken: {args['pageToken']!r}", category="invalid_input")

    rows = db.query("SELECT * FROM calendar_calendars ORDER BY primary_calendar DESC, calendar_id ASC")
    calendars = [
        {
            "id": r["calendar_id"],
            "summary": r["summary"],
            "description": r["description"],
            "timeZone": r["time_zone"],
            "accessRole": r["access_role"],
        }
        for r in rows
    ]
    page = calendars[offset : offset + page_size]
    result = {"calendars": page}
    if offset + page_size < len(calendars):
        result["nextPageToken"] = str(offset + page_size)
    return result


def _caller(db):
    """Who is in this conversation, as the session set it: ("contact", row) for a prospect speaking
    for themselves, ("staff", row) for a BrightDesk employee (their Slack user), or (None, None).
    An ID or an email the desk is told never changes it."""
    row = db.one("SELECT person_id FROM session_caller WHERE id = 'default'")
    person = row["person_id"] if row else None
    if person:
        contact = db.one("SELECT * FROM contacts WHERE contact_id = ?", [person])
        if contact:
            return "contact", contact
        staff = db.one("SELECT * FROM slack_users WHERE user_id = ?", [person])
        if staff:
            return "staff", staff
    return None, None


def _named(kind, who):
    if kind == "contact":
        return f"{who['name']} ({who['contact_id']})"
    if kind == "staff":
        return f"{who['name']} ({who['user_id']})"
    return "nobody signed in"
