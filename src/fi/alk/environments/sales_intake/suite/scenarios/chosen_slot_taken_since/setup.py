def setup(world):
    world.put("accounts", {
        "account_id": "ACC420",
        "name": "St. Laurent Telecom",
        "domain": "stlaurent.example",
        "country": "Canada",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("contacts", {
        "contact_id": "C420",
        "account_id": "ACC420",
        "name": "Genevieve Bouchard",
        "email": "genevieve.bouchard@stlaurent.example",
        "email_normalized": "genevieve.bouchard@stlaurent.example",
        "title": "Support Operations Lead",
        "country": "Canada",
        "consent_status": "opted_in",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("leads", {
        "lead_id": "L420",
        "contact_id": "C420",
        "account_id": "ACC420",
        "country": "Canada",
        "use_case": "customer_service",
        "seats": 60,
        "timeline": "3-6 months",
        "budget": "confirmed",
        "source": "email",
        "status": "qualified",
        "owner_rep_id": "A02",
        "queue_id": None,
        "request_key": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("calendar_events", {
        "event_id": "EV-S410",
        "calendar_id": "CAL-A02",
        "summary": "Customer escalation",
        "description": None,
        "location": "Video call",
        "start_time": "2026-11-12T20:00:00Z",
        "end_time": "2026-11-12T20:30:00Z",
        "time_zone": "America/Toronto",
        "status": "confirmed",
        "version": 1,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("gmail_threads", {
        "id": "th_s410_mapleridge_pick",
        "history_id": "shist_410",
        "subject": "Re: St. Laurent Demo times",
        "snippet": "I'd like to book Thursday Nov 12 at 3:00 PM Toronto time (DS06).",
        "updated_at": "2026-11-09T10:00:00Z",
        "is_restricted": 0,
    })
    world.put("gmail_messages", {
        "id": "smsg_410",
        "thread_id": "th_s410_mapleridge_pick",
        "sender": "genevieve.bouchard@stlaurent.example",
        "recipient": "sales@brightdesk.example",
        "subject": "Re: St. Laurent Demo times",
        "snippet": "I'd like to book Thursday Nov 12 at 3:00 PM Toronto time (DS06).",
        "body": "I'd like to book Thursday Nov 12 at 3:00 PM Toronto time (DS06).",
        "date": "2026-11-09T10:00:00Z",
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s410_mapleridge_pick",
        "label_id": "INBOX",
    })
