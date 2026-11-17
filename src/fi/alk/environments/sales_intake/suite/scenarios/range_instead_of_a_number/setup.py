def setup(world):
    # Avril Bouchard is a second contact at Quillon Travel (ACC11), which is already on file.
    world.put("contacts", {
        "contact_id": "C425",
        "account_id": "ACC11",
        "name": "Avril Bouchard",
        "email": "avril.bouchard@quillon.example",
        "email_normalized": "avril.bouchard@quillon.example",
        "title": "Operations Lead",
        "country": "United States",
        "consent_status": "opted_in",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("leads", {
        "lead_id": "L425",
        "contact_id": "C425",
        "account_id": "ACC11",
        "country": "United States",
        "use_case": "support",
        "seats": None,
        "timeline": "0-3 months",
        "budget": None,
        "source": "chat",
        "status": "needs_information",
        "owner_rep_id": None,
        "queue_id": None,
        "request_key": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "C425"}, by="id")
