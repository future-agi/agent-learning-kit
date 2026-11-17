def handle(args, db):
    page_size = args.get("pageSize")
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
