def setup(world):
    world.put("customers", {"customer_id": "C116", "first_name": "Fiona", "last_name": "MacLeod", "email": "fiona.m@example.co.uk", "phone": "+442079460116", "passcode": "7216", "kba_answer": "Edinburgh", "otp_code": "829104"})
    world.put("accounts", {"account_id": "ACC116", "customer_id": "C116", "account_number": "1000000116", "account_type": "checking", "balance": 450000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD116", "account_id": "ACC116", "customer_id": "C116", "card_number_masked": "••••••••••••7216", "last_four": "7216", "card_type": "debit", "status": "active", "expiration_date": "2027-04-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS116", "customer_id": "C116", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:40:00Z"})
