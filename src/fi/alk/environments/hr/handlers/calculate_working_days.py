def handle(args, db):
    import datetime
    
    start_str = args.get("start_date")
    end_str = args.get("end_date")
    region = args.get("region")
    
    if not start_str or not end_str or not region:
        raise ToolError("start_date, end_date, and region are required.")
        
    try:
        start_dt = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()
    except Exception as e:
        raise ToolError(f"Invalid date format: must be YYYY-MM-DD. Error: {e}")
        
    if start_dt > end_dt:
        raise ToolError(f"start_date ({start_str}) cannot be after end_date ({end_str}).")
        
    holidays_rows = db.query("SELECT holiday_date, name FROM holiday_calendars WHERE region = ? AND holiday_date >= ? AND holiday_date <= ?", (region, start_str, end_str))
    holiday_map = {r["holiday_date"]: r["name"] for r in holidays_rows}
    
    curr = start_dt
    one_day = datetime.timedelta(days=1)
    
    total_days = 0
    weekend_days = 0
    holiday_days = 0
    holidays_encountered = []
    working_days = 0
    
    while curr <= end_dt:
        total_days += 1
        curr_str = curr.strftime("%Y-%m-%d")
        if curr.weekday() in (5, 6):
            weekend_days += 1
        elif curr_str in holiday_map:
            holiday_days += 1
            holidays_encountered.append({"date": curr_str, "name": holiday_map[curr_str]})
        else:
            working_days += 1
        curr += one_day
        
    return {
        "start_date": start_str,
        "end_date": end_str,
        "region": region,
        "total_calendar_days": total_days,
        "weekend_days": weekend_days,
        "holiday_days": holiday_days,
        "holidays_encountered": holidays_encountered,
        "working_days": working_days
    }
