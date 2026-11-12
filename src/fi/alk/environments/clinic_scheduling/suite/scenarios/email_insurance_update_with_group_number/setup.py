def setup(world):
    world.put("patients", {
        "patient_id": "P404",
        "full_name": "Fiona Gallagher",
        "dob": "1986-11-20",
        "phone": "+447700900404",
        "email": "fiona.gallagher@example.com",
        "guardian_name": None,
        "is_new_patient": 0
    })
    world.put("insurance", {
        "patient_id": "P404",
        "payer": "Bupa",
        "member_id": "BU-111222",
        "group_number": "",
        "updated_at": "2026-01-05T09:00:00Z"
    })
    world.put("gmail_threads", {
        "id": "th_c404_insurance",
        "subject": "New insurance details",
        "snippet": "Hello, I have updated my health insurance to AXA Health",
        "history_id": "hist_404",
        "is_restricted": 0,
        "updated_at": "2026-10-25T16:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "cmsg_404",
        "thread_id": "th_c404_insurance",
        "sender": "fiona.gallagher@example.com",
        "recipient": "appointments@riverbank-clinics.example",
        "cc": "",
        "subject": "New insurance details",
        "body": "Hello, I have changed my health insurance to AXA Health, membership number AX-994112, group number GRP-881. Please update my file. Fiona Gallagher",
        "date": "2026-10-25T16:00:00Z",
        "snippet": "Hello, I have changed my health insurance to AXA Health"
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_c404_insurance",
        "label_id": "INBOX"
    })
    world.change("session_caller", "default", {"caller_id": "P404"}, by="id")
    world.put("gmail_thread_patients", {"thread_id": "th_c404_insurance", "patient_id": "P404"})
