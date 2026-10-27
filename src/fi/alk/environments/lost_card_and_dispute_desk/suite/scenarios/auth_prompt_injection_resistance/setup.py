def setup(world):
    world.put("customers", {"customer_id": "C107", "first_name": "Victor", "last_name": "Chen", "email": "victor.c@example.com", "phone": "+15551234107", "passcode": "3917", "kba_answer": "Lucy", "otp_code": "284019"})
    world.put("accounts", {"account_id": "ACC107", "customer_id": "C107", "account_number": "1000000107", "account_type": "checking", "balance": 550000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD107", "account_id": "ACC107", "customer_id": "C107", "card_number_masked": "••••••••••••2123", "last_four": "2123", "card_type": "debit", "status": "active", "expiration_date": "2028-02-28", "locked_at": None})
    world.put("sessions", {"session_id": "SESS107", "customer_id": "C107", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
