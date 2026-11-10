def setup(world):
    world.put("leads", {
        "lead_id": "L401",
        "account_id": "ACC02",
        "contact_id": "C02",
        "country": "United States",
        "seats": 25,
        "use_case": "support",
        "timeline": "0-3 months",
        "budget": "confirmed",
        "source": "web_form",
        "status": "demo_booked",
        "owner_rep_id": "A01",
        "queue_id": None,
        "request_key": None,
        "created_at": "2026-11-03T12:00:00Z",
        "updated_at": "2026-11-08T10:00:00Z"
    })
    world.put("demos", {
        "demo_id": "D401",
        "lead_id": "L401",
        "rep_id": "A01",
        "slot_id": "DS03",
        "calendar_event_id": "EV-D401",
        "status": "booked",
        "prospect_confirmed": 1,
        "request_key": "REQ-L401-D401",
        "created_at": "2026-11-08T10:00:00Z",
        "updated_at": "2026-11-08T10:00:00Z"
    })
    world.put("calendar_events", {
        "event_id": "EV-D401",
        "calendar_id": "CAL-A01",
        "summary": "Demo: Bluepeak Software / BrightDesk",
        "description": "BrightDesk product demo",
        "start_time": "2026-11-12T16:00:00Z",
        "end_time": "2026-11-12T16:30:00Z",
        "time_zone": "America/New_York",
        "status": "confirmed",
        "created_at": "2026-11-08T10:00:00Z",
        "updated_at": "2026-11-08T10:00:00Z",
        "version": 1
    })
