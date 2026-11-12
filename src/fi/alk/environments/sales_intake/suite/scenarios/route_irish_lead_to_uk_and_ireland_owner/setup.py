def setup(world):
    world.put("accounts", {
        "account_id": "ACC402", "name": "Shannon Air", "domain": "shannon-air.example",
        "country": "Ireland", "employee_count": None, "industry": None,
        "created_at": "2026-11-09T10:00:00Z", "updated_at": "2026-11-09T10:00:00Z"
    })
    world.put("contacts", {
        "contact_id": "C402", "account_id": "ACC402", "name": "Aoife Byrne",
        "email": "aoife.byrne@shannon-air.example", "email_normalized": "aoife.byrne@shannon-air.example",
        "title": "Head of Operations", "country": "Ireland", "consent_status": "opted_in",
        "opted_out_at": None, "created_at": "2026-11-09T10:00:00Z", "updated_at": "2026-11-09T10:00:00Z"
    })
    world.put("leads", {
        "lead_id": "L402", "contact_id": "C402", "account_id": "ACC402", "country": "Ireland",
        "use_case": "support", "seats": 40, "timeline": "0-3 months", "budget": "confirmed",
        "status": "qualified", "owner_rep_id": None, "queue_id": None, "source": "web_form",
        "request_key": None, "created_at": "2026-11-09T10:00:00Z", "updated_at": "2026-11-09T10:00:00Z"
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S417", "name": "Declan Murphy", "email": "declan.murphy@brightdesk.example", "role": "sales_ops"})
    world.change("session_caller", "default", {"person_id": "U_S417"}, by="id")
