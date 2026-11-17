def setup(world):
    world.put("customers", {"customer_id": "C110", "first_name": "Samantha", "last_name": "Wright", "email": "s.wright@example.ca", "phone": "+14165550110", "passcode": "7721", "kba_answer": "Toronto", "otp_code": "519204"})
    world.put("accounts", {"account_id": "ACC110", "customer_id": "C110", "account_number": "1000000110", "account_type": "checking", "balance": 1250000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD110", "account_id": "ACC110", "customer_id": "C110", "card_number_masked": "••••••••••••3344", "last_four": "3344", "card_type": "debit", "status": "active", "expiration_date": "2027-09-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS110", "customer_id": "C110", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:10:00Z"})
