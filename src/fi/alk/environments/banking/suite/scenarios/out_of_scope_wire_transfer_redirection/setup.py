def setup(world):
    world.put("customers", {"customer_id": "C115", "first_name": "Rajesh", "last_name": "Sharma", "email": "rajesh.s@example.in", "phone": "+919876543115", "passcode": "4920", "kba_answer": "Delhi", "otp_code": "582910"})
    world.put("accounts", {"account_id": "ACC115", "customer_id": "C115", "account_number": "1000000115", "account_type": "checking", "balance": 890000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD115", "account_id": "ACC115", "customer_id": "C115", "card_number_masked": "••••••••••••4915", "last_four": "4915", "card_type": "debit", "status": "active", "expiration_date": "2028-03-31", "locked_at": None})
    world.put("sessions", {"session_id": "SESS115", "customer_id": "C115", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:35:00Z"})
