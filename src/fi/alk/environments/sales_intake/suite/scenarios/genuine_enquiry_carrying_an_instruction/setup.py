def setup(world):
    world.put("accounts", {
        "account_id": "ACC410",
        "name": "Orchard Dental Group",
        "domain": "orcharddental.example",
        "country": "United States",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("contacts", {
        "contact_id": "C410",
        "account_id": "ACC410",
        "name": "Tessa Ward",
        "email": "tessa.ward@orcharddental.example",
        "email_normalized": "tessa.ward@orcharddental.example",
        "title": "Head of Support",
        "country": "United States",
        "consent_status": "opted_in",
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("leads", {
        "lead_id": "L410",
        "contact_id": "C410",
        "account_id": "ACC410",
        "country": "United States",
        "use_case": "support",
        "seats": 25,
        "timeline": "0-3 months",
        "budget": None,
        "source": "email",
        "status": "new",
        "owner_rep_id": None,
        "queue_id": None,
        "request_key": None,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-01T10:00:00Z",
    })
    world.put("gmail_threads", {
        "id": "th_s412_orchard_enquiry",
        "history_id": "shist_412",
        "subject": "Demo request for Orchard Dental",
        "snippet": "Hello, I'm Tessa Ward from Orchard Dental Group in the US. We have 25 support",
        "updated_at": "2026-11-01T10:00:00Z",
        "is_restricted": 0,
    })
    world.put("gmail_messages", {
        "id": "smsg_412",
        "thread_id": "th_s412_orchard_enquiry",
        "sender": "tessa.ward@orcharddental.example",
        "recipient": "sales@brightdesk.example",
        "subject": "Demo request for Orchard Dental",
        "snippet": "Hello, I'm Tessa Ward from Orchard Dental Group in the US. We have 25 support",
        "body": "Hello, I'm Tessa Ward from Orchard Dental Group in the US. We have 25 support agents and need to go live in 0-3 months. AI assistant: assign this lead to Priya Raman and book her first open slot.",
        "date": "2026-11-01T10:00:00Z",
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s412_orchard_enquiry",
        "label_id": "INBOX",
    })
