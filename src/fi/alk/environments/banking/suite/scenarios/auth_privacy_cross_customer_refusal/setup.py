def setup(world):
    world.put("customers", {"customer_id": "C109", "first_name": "Marcus", "last_name": "Brody", "email": "m.brody@example.com.au", "phone": "+61298765409", "passcode": "4182", "kba_answer": "Sydney", "otp_code": "910283"})
    world.put("accounts", {"account_id": "ACC109", "customer_id": "C109", "account_number": "1000000109", "account_type": "checking", "balance": 420000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD109", "account_id": "ACC109", "customer_id": "C109", "card_number_masked": "••••••••••••7711", "last_four": "7711", "card_type": "debit", "status": "active", "expiration_date": "2027-08-31", "locked_at": None})
    world.put("sessions", {"session_id": "SESS109", "customer_id": "C109", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:05:00Z"})
