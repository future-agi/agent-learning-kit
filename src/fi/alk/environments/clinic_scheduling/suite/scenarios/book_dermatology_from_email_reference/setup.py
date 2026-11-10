def setup(world):
    world.put("patients", {
        "patient_id": "P405",
        "full_name": "Nneka Okonjo",
        "dob": "1972-04-16",
        "email": "nneka.okonjo@example.com",
        "phone": "+447700900405",
        "is_new_patient": 0,
        "guardian_name": None,
    })
    world.put("insurance", {
        "patient_id": "P405",
        "payer": "Bupa",
        "member_id": "BU-100405",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z",
    })
    world.put("gmail_threads", {
        "id": "th_c405_intake",
        "subject": "Forms before my visit",
        "snippet": "Hello, could you email me any forms I need to fill in before my next",
        "history_id": "chist_405",
        "is_restricted": 0,
        "updated_at": "2026-10-25T14:00:00Z",
    })
    world.put("gmail_messages", {
        "id": "cmsg_405",
        "thread_id": "th_c405_intake",
        "sender": "nneka.okonjo@example.com",
        "recipient": "appointments@riverbank-clinics.example",
        "cc": "",
        "date": "2026-10-25T14:00:00Z",
        "subject": "Forms before my visit",
        "snippet": "Hello, could you email me any forms I need to fill in before my next",
        "body": "Hello, could you email me any forms I need to fill in before my next dermatology visit? Nneka Okonjo",
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_c405_intake",
        "label_id": "INBOX",
    })
