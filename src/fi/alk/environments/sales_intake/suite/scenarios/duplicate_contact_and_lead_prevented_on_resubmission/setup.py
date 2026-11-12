def setup(world):
    world.put("accounts", {
        "account_id": "ACC_SOLARIS",
        "name": "Solaris Cloud",
        "domain": "solaris-cloud.example",
        "country": "Canada",
        "employee_count": None,
        "industry": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z"
    })
    world.put("contacts", {
        "contact_id": "C_DEVON",
        "account_id": "ACC_SOLARIS",
        "name": "Devon Price",
        "email": "devon.price@solaris-cloud.example",
        "email_normalized": "devon.price@solaris-cloud.example",
        "title": "IT Support Manager",
        "country": "Canada",
        "consent_status": "opted_in",
        "opted_out_at": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z"
    })
    world.put("leads", {
        "lead_id": "L_DEVON",
        "contact_id": "C_DEVON",
        "account_id": "ACC_SOLARIS",
        "country": "Canada",
        "seats": 20,
        "use_case": "helpdesk",
        "timeline": None,
        "budget": None,
        "status": "new",
        "source": "web_form",
        "owner_rep_id": None,
        "queue_id": None,
        "request_key": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z"
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "C_DEVON"}, by="id")
