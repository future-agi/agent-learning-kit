def setup(world):
    world.put("customers", {"customer_id": "C108", "first_name": "Elena", "last_name": "Rostova", "email": "elena.r@example.co.uk", "phone": "+442079460108", "passcode": "6421", "kba_answer": "Oxford", "otp_code": "829103"})
    world.put("accounts", {"account_id": "ACC108", "customer_id": "C108", "account_number": "1000000108", "account_type": "checking", "balance": 980000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD108", "account_id": "ACC108", "customer_id": "C108", "card_number_masked": "••••••••••••9012", "last_four": "9012", "card_type": "debit", "status": "active", "expiration_date": "2027-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS108", "customer_id": "C108", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:00:00Z"})
