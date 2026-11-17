def handle(args, db):
    region = args.get("region")
    year = args.get("year")
    if not region:
        raise ToolError("region is required.")
    if region not in ("US", "UK", "APAC"):
        raise ToolError(f"Unknown region {region!r}; use US, UK or APAC.")
    if year is not None and (not isinstance(year, int) or isinstance(year, bool)):
        raise ToolError("year must be an integer, e.g. 2026.")
    rows = db.query("SELECT holiday_date, name FROM holiday_calendars WHERE region = ? ORDER BY holiday_date", (region,))
    if year:
        rows = [r for r in rows if r["holiday_date"].startswith(str(year))]
    today = (db.one("SELECT now FROM clock") or {"now": "2026-10-26T09:00:00Z"})["now"][:10]
    return {"region": region, "year": year, "holidays": [{"date": r["holiday_date"], "name": r["name"], "upcoming": r["holiday_date"] >= today} for r in rows]}
