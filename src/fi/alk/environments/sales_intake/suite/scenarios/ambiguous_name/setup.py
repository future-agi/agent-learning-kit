def setup(world):
    world.put("accounts", {
        "account_id": "ACC411",
        "name": "Doyle Marine",
        "domain": "doylemarine.example",
        "country": "Ireland",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("contacts", {
        "contact_id": "C411",
        "account_id": "ACC411",
        "name": "Sean Doyle",
        "email": "sean.doyle@doylemarine.example",
        "email_normalized": "sean.doyle@doylemarine.example",
        "title": "Operations Director",
        "country": "Ireland",
        "consent_status": "opted_in",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
