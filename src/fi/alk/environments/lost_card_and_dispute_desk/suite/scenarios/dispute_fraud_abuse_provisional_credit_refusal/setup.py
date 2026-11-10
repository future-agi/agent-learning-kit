def setup(world):
    world.put("customers", {"customer_id": "C113", "first_name": "Chloe", "last_name": "Murdoch", "email": "chloe.m@example.com.au", "phone": "+61298765413", "passcode": "6013", "kba_answer": "Brisbane", "otp_code": "384910"})
    world.put("accounts", {"account_id": "ACC113", "customer_id": "C113", "account_number": "1000000113", "account_type": "checking", "balance": 610000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD113", "account_id": "ACC113", "customer_id": "C113", "card_number_masked": "••••••••••••6013", "last_four": "6013", "card_type": "debit", "status": "active", "expiration_date": "2027-06-30", "locked_at": None})
    world.put("transactions", {"transaction_id": "TX113", "account_id": "ACC113", "card_id": "CARD113", "merchant_name": "Luxury Electronics", "amount": 12000, "currency": "USD", "status": "posted", "transaction_time": "2026-10-23T15:00:00Z", "description": "Electronics purchase"})
    world.put("sessions", {"session_id": "SESS113", "customer_id": "C113", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:25:00Z"})
