

def handle(args, db):
    if args.get("location_id"):
        rows = db.query("SELECT * FROM locations WHERE location_id = ?", [args["location_id"]])
        if not rows:
            raise ToolError(f"Location {args['location_id']!r} not found.", category="not_found")
    else:
        rows = db.query("SELECT * FROM locations ORDER BY location_id")
    return {"locations": [dict(r) for r in rows], "count": len(rows)}
