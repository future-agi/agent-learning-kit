def setup(world):
    world.put("accounts", {
        "account_id": "ACC401", "name": "Kaleo Care", "domain": "kaleo-care.example",
        "country": "United States", "employee_count": None, "industry": None,
        "created_at": "2026-11-09T10:00:00Z", "updated_at": "2026-11-09T10:00:00Z"
    })
    world.put("contacts", {
        "contact_id": "C401", "account_id": "ACC401", "name": "Ruth Abebe",
        "email": "ruth.abebe@kaleo-care.example", "email_normalized": "ruth.abebe@kaleo-care.example",
        "title": "Director of Support", "country": "United States", "consent_status": "opted_in",
        "opted_out_at": None, "created_at": "2026-11-09T10:00:00Z", "updated_at": "2026-11-09T10:00:00Z"
    })
    world.put("leads", {
        "lead_id": "L401", "contact_id": "C401", "account_id": "ACC401", "country": "United States",
        "use_case": "support", "seats": 30, "timeline": "0-3 months", "budget": "not confirmed",
        "status": "new", "owner_rep_id": None, "queue_id": None, "source": "web_form",
        "request_key": None, "created_at": "2026-11-09T10:00:00Z", "updated_at": "2026-11-09T10:00:00Z"
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "C401"}, by="id")
