def setup(world):
    world.put("accounts", {
        "account_id": "ACC_ORION",
        "name": "Orion Logistics UK",
        "domain": "orion-logistics.example",
        "country": "United Kingdom",
        "employee_count": None,
        "industry": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z"
    })
    world.put("contacts", {
        "contact_id": "C_BEATRICE",
        "account_id": "ACC_ORION",
        "name": "Beatrice Vance",
        "email": "beatrice.vance@orion-logistics.example",
        "email_normalized": "beatrice.vance@orion-logistics.example",
        "title": "Head of Customer Care",
        "country": "United Kingdom",
        "consent_status": "opted_in",
        "opted_out_at": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z"
    })
    world.put("leads", {
        "lead_id": "L_BEATRICE",
        "contact_id": "C_BEATRICE",
        "account_id": "ACC_ORION",
        "country": "United Kingdom",
        "seats": 50,
        "use_case": "customer_service",
        "timeline": "0-3 months",
        "budget": "confirmed",
        "status": "new",
        "source": "chat",
        "owner_rep_id": None,
        "queue_id": None,
        "request_key": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z"
    })
