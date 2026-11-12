def setup(world):
    world.put("contacts", {
        "contact_id": "C421",
        "account_id": "ACC10",
        "name": "Nigel Caldwell",
        "email": "nigel.caldwell@tasmanhealth.example",
        "email_normalized": "nigel.caldwell@tasmanhealth.example",
        "title": "Operations Director",
        "country": "Australia",
        "consent_status": "opted_in",
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-01T10:00:00Z",
    })
    world.put("leads", {
        "lead_id": "L421",
        "contact_id": "C421",
        "account_id": "ACC10",
        "source": "web_form",
        "use_case": "helpdesk",
        "seats": 40,
        "timeline": "0-3 months",
        "country": "Australia",
        "budget": "confirmed",
        "status": "demo_booked",
        "owner_rep_id": "A06",
        "queue_id": None,
        "request_key": "REQ-L421",
        "created_at": "2026-11-03T12:00:00Z",
        "updated_at": "2026-11-05T12:00:00Z",
    })
    world.put("calendar_events", {
        "event_id": "EV-D421",
        "calendar_id": "CAL-A06",
        "summary": "BrightDesk demo: Tasman Health",
        "description": "Demo for Nigel Caldwell",
        "location": "Video call",
        "start_time": "2026-11-12T23:00:00Z",
        "end_time": "2026-11-12T23:30:00Z",
        "time_zone": "Australia/Sydney",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-11-06T09:00:00Z",
        "updated_at": "2026-11-06T09:00:00Z",
    })
    world.put("demos", {
        "demo_id": "D421",
        "lead_id": "L421",
        "rep_id": "A06",
        "slot_id": "DS421",
        "status": "booked",
        "prospect_confirmed": 1,
        "request_key": "REQ-L421-DEMO",
        "calendar_event_id": "EV-D421",
        "created_at": "2026-11-06T09:00:00Z",
        "updated_at": "2026-11-06T09:00:00Z",
    })
    # Its own slot: DS17 belongs to the seed demo D01.
    world.put("demo_slots", {"slot_id": "DS421", "rep_id": "A06", "start_time": "2026-11-12T23:00:00Z", "end_time": "2026-11-12T23:30:00Z", "status": "booked"})
