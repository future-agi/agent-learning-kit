def setup(world):
    world.put("gmail_threads", {
        "id": "th_s401_bluepeak",
        "snippet": "Hello, could you provide pricing for 25 seats? Marcus Bell",
        "subject": "Pricing enquiry for 25 seats",
        "updated_at": "2026-11-09T10:00:00Z",
        "history_id": "shist_401",
        "is_restricted": 0
    })
    world.put("gmail_messages", {
        "id": "smsg_401",
        "thread_id": "th_s401_bluepeak",
        "sender": "marcus.bell@bluepeak.example",
        "recipient": "sales@brightdesk.example",
        "subject": "Pricing enquiry for 25 seats",
        "body": "Hello, could you provide pricing for 25 seats? Marcus Bell",
        "snippet": "Hello, could you provide pricing for 25 seats? Marcus Bell",
        "date": "2026-11-09T10:00:00Z",
        "cc": ""
    })
    world.put("gmail_thread_labels", {"thread_id": "th_s401_bluepeak", "label_id": "INBOX"})
    world.put("gmail_thread_labels", {"thread_id": "th_s401_bluepeak", "label_id": "enquiries"})