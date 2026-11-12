def setup(world):
    world.put("gmail_drafts", {
        "id": "D-401",
        "thread_id": "th_r13_store_credit",
        "recipient": "rachel.green@example.com",
        "cc": "",
        "bcc": "",
        "subject": "Re: How much store credit do I have",
        "body": "Hi Rachel, you currently have $25.00 in store credit expiring Oct 15, 2027.",
        "is_human_edited": 1,
        "created_at": "2026-10-25T11:00:00Z",
        "updated_at": "2026-10-25T11:00:00Z",
    })
    # The person in this conversation, as the session signs them in.
    world.change("session_caller", "default", {"customer_id": "C18"}, by="id")
