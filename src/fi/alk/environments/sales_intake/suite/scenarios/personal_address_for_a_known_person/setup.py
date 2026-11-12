def setup(world):
    world.put("accounts", {
        "account_id": "ACC423",
        "name": "Kestrel Logistics",
        "domain": "kestrellogistics.example",
        "country": "United States",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("contacts", {
        "contact_id": "C423",
        "account_id": "ACC423",
        "name": "Brooke Winslow",
        "email": "brooke.winslow@kestrellogistics.example",
        "email_normalized": "brooke.winslow@kestrellogistics.example",
        "title": "Director of Customer Care",
        "country": "United States",
        "consent_status": "opted_in",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("leads", {
        "lead_id": "L423",
        "contact_id": "C423",
        "account_id": "ACC423",
        "country": "United States",
        "use_case": "support",
        "seats": 65,
        "timeline": "0-3 months",
        "budget": "confirmed",
        "source": "web_form",
        "status": "qualified",
        "owner_rep_id": "A01",
        "queue_id": None,
        "request_key": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "C423"}, by="id")
