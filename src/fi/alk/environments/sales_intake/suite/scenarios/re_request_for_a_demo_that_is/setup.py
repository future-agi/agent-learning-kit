def setup(world):
    world.put("contacts", {
        "contact_id": "C418",
        "account_id": "ACC10",
        "name": "Callum O'Donnell",
        "email": "callum.odonnell@tasmanhealth.example",
        "email_normalized": "callum.odonnell@tasmanhealth.example",
        "title": "Operations Director",
        "country": "Australia",
        "consent_status": "opted_in",
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-01T10:00:00Z",
    })
    world.put("leads", {
        "lead_id": "L418",
        "contact_id": "C418",
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
        "request_key": "REQ-L418",
        "created_at": "2026-11-03T12:00:00Z",
        "updated_at": "2026-11-05T12:00:00Z",
    })
    world.put("calendar_events", {
        "event_id": "EV-D418",
        "calendar_id": "CAL-A06",
        "summary": "BrightDesk demo: Tasman Health",
        "description": "Demo for Callum O'Donnell",
        "location": "Video call",
        "start_time": "2026-11-13T02:00:00Z",
        "end_time": "2026-11-13T02:30:00Z",
        "time_zone": "Australia/Sydney",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-11-06T09:00:00Z",
        "updated_at": "2026-11-06T09:00:00Z",
    })
    world.put("demos", {
        "demo_id": "D418",
        "lead_id": "L418",
        "rep_id": "A06",
        "slot_id": "DS418",
        "status": "booked",
        "prospect_confirmed": 1,
        "request_key": "REQ-L418-DEMO",
        "calendar_event_id": "EV-D418",
        "created_at": "2026-11-06T09:00:00Z",
        "updated_at": "2026-11-06T09:00:00Z",
    })
    # Its own slot: DS17 belongs to the seed demo D01.
    world.put("demo_slots", {"slot_id": "DS418", "rep_id": "A06", "start_time": "2026-11-13T02:00:00Z", "end_time": "2026-11-13T02:30:00Z", "status": "booked"})
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "C418"}, by="id")
