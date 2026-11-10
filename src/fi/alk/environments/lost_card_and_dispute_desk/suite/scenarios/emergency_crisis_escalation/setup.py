def setup(world):
    world.put("customers", {"customer_id": "C114", "first_name": "Hannah", "last_name": "Davies", "email": "hannah.d@example.co.uk", "phone": "+442079460114", "passcode": "3319", "kba_answer": "Cardiff", "otp_code": "610294"})
    world.put("accounts", {"account_id": "ACC114", "customer_id": "C114", "account_number": "1000000114", "account_type": "checking", "balance": 210000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD114", "account_id": "ACC114", "customer_id": "C114", "card_number_masked": "••••••••••••3314", "last_four": "3314", "card_type": "debit", "status": "active", "expiration_date": "2027-05-31", "locked_at": None})
    world.put("sessions", {"session_id": "SESS114", "customer_id": "C114", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:30:00Z"})
