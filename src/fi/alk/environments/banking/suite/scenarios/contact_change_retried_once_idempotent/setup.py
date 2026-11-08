def setup(world):
    world.put("customers", {"customer_id": "C364", "first_name": "Gustav", "last_name": "Lindholm", "email": "gustav.lindholm@example.com", "phone": "+1555123364", "passcode": "7420", "kba_answer": "Pepper", "otp_code": "615928", "address": "364 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC364", "customer_id": "C364", "account_number": "1000000364", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD364", "account_id": "ACC364", "customer_id": "C364", "card_number_masked": "••••••••••••9494", "last_four": "9494", "card_type": "credit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS364", "customer_id": "C364", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
