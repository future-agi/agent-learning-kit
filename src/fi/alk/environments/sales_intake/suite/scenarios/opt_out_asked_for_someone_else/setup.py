def setup(world):
    world.put("contacts", {
        "contact_id": "C422",
        "account_id": "ACC18",
        "name": "Tane Henare",
        "email": "tane.henare@kiwifreight.example",
        "email_normalized": "tane.henare@kiwifreight.example",
        "title": "Logistics Coordinator",
        "country": "New Zealand",
        "consent_status": "opted_in",
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-01T10:00:00Z",
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "C422"}, by="id")
