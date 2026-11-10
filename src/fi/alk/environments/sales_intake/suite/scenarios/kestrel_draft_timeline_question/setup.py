def setup(world):
    world.put("gmail_threads", {
        "id": "th_s402_kestrel_helpdesk",
        "snippet": "Hi, Katrin Vogel from Kestrel Mobility. We need helpdesk for 44 seats.",
        "subject": "Kestrel Mobility helpdesk enquiry",
        "updated_at": "2026-11-08T11:00:00Z",
        "history_id": "shist_402",
        "is_restricted": 0
    })
    world.put("gmail_messages", {
        "id": "smsg_402",
        "thread_id": "th_s402_kestrel_helpdesk",
        "sender": "katrin.vogel@kestrel-mobility.example",
        "recipient": "sales@brightdesk.example",
        "subject": "Kestrel Mobility helpdesk enquiry",
        "body": "Hi, Katrin Vogel from Kestrel Mobility here. We are looking for helpdesk software for 44 seats in Germany.",
        "snippet": "Hi, Katrin Vogel from Kestrel Mobility here.",
        "date": "2026-11-08T11:00:00Z",
        "cc": ""
    })
    world.put("gmail_thread_labels", {"thread_id": "th_s402_kestrel_helpdesk", "label_id": "INBOX"})
    world.put("gmail_thread_labels", {"thread_id": "th_s402_kestrel_helpdesk", "label_id": "enquiries"})