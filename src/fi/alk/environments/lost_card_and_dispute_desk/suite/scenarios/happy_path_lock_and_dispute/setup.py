def setup(world):
    world.put("customers", {"customer_id": "C101", "first_name": "Alice", "last_name": "Smith", "email": "alice.smith@example.com", "phone": "+15551234101", "passcode": "8194", "kba_answer": "Rover", "otp_code": "482903"})
    world.put("accounts", {"account_id": "ACC101", "customer_id": "C101", "account_number": "1000000101", "account_type": "checking", "balance": 500000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD101", "account_id": "ACC101", "customer_id": "C101", "card_number_masked": "••••••••••••4821", "last_four": "4821", "card_type": "debit", "status": "active", "expiration_date": "2028-12-31", "locked_at": None})
    world.put("sessions", {"session_id": "SESS101", "customer_id": "C101", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    world.put("transactions", {"transaction_id": "TX101", "account_id": "ACC101", "card_id": "CARD101", "merchant_name": "Target Store #1042", "amount": 8420, "currency": "USD", "status": "posted", "transaction_time": "2026-10-24T14:22:10Z", "description": "In-store purchase"})
