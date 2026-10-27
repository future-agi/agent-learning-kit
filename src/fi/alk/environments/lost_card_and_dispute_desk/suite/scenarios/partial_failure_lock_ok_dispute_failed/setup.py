def setup(world):
    world.put("customers", {"customer_id": "C105", "first_name": "Evan", "last_name": "Davis", "email": "evan.d@example.com", "phone": "+15551234105", "passcode": "1839", "kba_answer": "Luna", "otp_code": "592014"})
    world.put("accounts", {"account_id": "ACC105", "customer_id": "C105", "account_number": "1000000105", "account_type": "checking", "balance": 480000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD105", "account_id": "ACC105", "customer_id": "C105", "card_number_masked": "••••••••••••7789", "last_four": "7789", "card_type": "debit", "status": "active", "expiration_date": "2027-04-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS105", "customer_id": "C105", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    world.put("transactions", {"transaction_id": "TX105", "account_id": "ACC105", "card_id": "CARD105", "merchant_name": "Best Buy", "amount": 12000, "currency": "USD", "status": "posted", "transaction_time": "2026-10-25T15:30:00Z", "description": "Electronics purchase"})
