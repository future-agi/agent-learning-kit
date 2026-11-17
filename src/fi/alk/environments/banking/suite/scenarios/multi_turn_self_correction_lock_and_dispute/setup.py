def setup(world):
    world.put("customers", {"customer_id": "C117", "first_name": "David", "last_name": "Walsh", "email": "d.walsh@example.com.au", "phone": "+61298765417", "passcode": "5517", "kba_answer": "Adelaide", "otp_code": "491028"})
    world.put("accounts", {"account_id": "ACC117", "customer_id": "C117", "account_number": "1000000117", "account_type": "checking", "balance": 520000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD117", "account_id": "ACC117", "customer_id": "C117", "card_number_masked": "••••••••••••5517", "last_four": "5517", "card_type": "debit", "status": "active", "expiration_date": "2027-07-31", "locked_at": None})
    world.put("transactions", {"transaction_id": "TX117", "account_id": "ACC117", "card_id": "CARD117", "merchant_name": "City Bookstore", "amount": 5400, "currency": "USD", "status": "posted", "transaction_time": "2026-10-22T11:00:00Z", "description": "Book purchase"})
    world.put("sessions", {"session_id": "SESS117", "customer_id": "C117", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:45:00Z"})
