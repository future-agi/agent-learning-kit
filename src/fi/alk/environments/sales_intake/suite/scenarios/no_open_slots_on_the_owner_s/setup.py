def setup(world):
    world.change("contacts", "C20", {"name": "Devendra Nambiar", "email": "devendra.nambiar@gangesfin.example", "email_normalized": "devendra.nambiar@gangesfin.example"}, by="contact_id")
    world.put("calendar_events", {
        "event_id": "EV-S411",
        "calendar_id": "CAL-A05",
        "summary": "Board prep",
        "description": None,
        "location": "Video call",
        "start_time": "2026-11-11T05:00:00Z",
        "end_time": "2026-11-11T06:00:00Z",
        "time_zone": "Asia/Kolkata",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("calendar_events", {
        "event_id": "EV-S412",
        "calendar_id": "CAL-A05",
        "summary": "Quarterly planning",
        "description": None,
        "location": "Video call",
        "start_time": "2026-11-12T09:00:00Z",
        "end_time": "2026-11-12T10:00:00Z",
        "time_zone": "Asia/Kolkata",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
