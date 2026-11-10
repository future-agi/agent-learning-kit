def setup(world):
    world.put("accounts", {
        "account_id": "ACC425",
        "name": "Quillon Travel",
        "domain": "quillon.example",
        "country": "Canada",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("contacts", {
        "contact_id": "C425",
        "account_id": "ACC425",
        "name": "Avril Bouchard",
        "email": "avril.bouchard@quillon.example",
        "email_normalized": "avril.bouchard@quillon.example",
        "title": "Operations Lead",
        "country": "Canada",
        "consent_status": "opted_in",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("leads", {
        "lead_id": "L425",
        "contact_id": "C425",
        "account_id": "ACC425",
        "country": "Canada",
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
