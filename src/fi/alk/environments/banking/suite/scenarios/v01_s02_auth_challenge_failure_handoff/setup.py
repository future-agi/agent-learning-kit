def setup(world):
    world.put("customers", {"customer_id": "C102", "first_name": "Bob", "last_name": "Hargreave", "email": "bob.hargreave@example.com", "phone": "+15551234102", "passcode": "7193", "kba_answer": "Fluffy", "otp_code": "918274"})
    world.put("accounts", {"account_id": "ACC102", "customer_id": "C102", "account_number": "1000000102", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD102", "account_id": "ACC102", "customer_id": "C102", "card_number_masked": "••••••••••••6038", "last_four": "6038", "card_type": "debit", "status": "active", "expiration_date": "2028-01-31", "locked_at": None})
    world.put("sessions", {"session_id": "SESS102", "customer_id": "C102", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
