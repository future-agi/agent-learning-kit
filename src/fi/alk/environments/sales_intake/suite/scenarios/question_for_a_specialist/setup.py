def setup(world):
    # Lukas Brandstetter is a second contact at Alpenwerk AG (ACC14), which is already on file.
    world.put("contacts", {
        "contact_id": "C424",
        "account_id": "ACC14",
        "name": "Lukas Brandstetter",
        "email": "lukas.brandstetter@alpenwerk.example",
        "email_normalized": "lukas.brandstetter@alpenwerk.example",
        "title": "Head of IT Security",
        "country": "Switzerland",
        "consent_status": "opted_in",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("leads", {
        "lead_id": "L424",
        "contact_id": "C424",
        "account_id": "ACC14",
        "country": "Switzerland",
        "use_case": "helpdesk",
        "seats": 120,
        "timeline": "3-6 months",
        "budget": "confirmed",
        "source": "web_form",
        "status": "qualified",
        "owner_rep_id": "A04",
        "queue_id": None,
        "request_key": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "C424"}, by="id")
