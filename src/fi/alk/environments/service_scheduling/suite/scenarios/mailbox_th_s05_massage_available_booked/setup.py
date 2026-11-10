def setup(world):
    world.put("customers", {
        "customer_id": "CU416",
        "name": "Callum Wilson",
        "email": "callum.wilson@example.com",
        "phone": "+44 20 7946 0416",
        "preferred_language": "en",
        "time_zone": "Europe/London",
        "created_at": "2026-10-26T09:00:00Z"
    })
    world.put("gmail_threads", {
        "id": "th_s403_callum_massage",
        "subject": "Massage availability Thursday",
        "snippet": "Is there any massage at Midtown on Thursday 29 October? If it is full, please",
        "history_id": "shist_403",
        "is_restricted": 0,
        "updated_at": "2026-10-25T18:00:00Z"
    })
    world.put("gmail_messages", {
        "id": "smsg_403",
        "thread_id": "th_s403_callum_massage",
        "sender": "callum.wilson@example.com",
        "recipient": "bookings@northlight-studios.example",
        "cc": "",
        "subject": "Massage availability Thursday",
        "body": "Is there any massage at Midtown on Thursday 29 October? If it is full, please put me on the waiting list. Callum Wilson",
        "date": "2026-10-25T18:00:00Z",
        "snippet": "Is there any massage at Midtown on Thursday 29 October? If it is full, please"
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s403_callum_massage",
        "label_id": "INBOX"
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s403_callum_massage",
        "label_id": "bookings"
    })
    world.put("gmail_thread_labels", {
        "thread_id": "th_s403_callum_massage",
        "label_id": "needs_reply"
    })
