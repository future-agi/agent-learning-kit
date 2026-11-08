def setup(world):
    world.put("customers", {"customer_id": "C360", "first_name": "Henry", "last_name": "Adebayo", "email": "henry.adebayo@example.com", "phone": "+1555123360", "passcode": "2719", "kba_answer": "Pepper", "otp_code": "640281", "address": "360 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC360", "customer_id": "C360", "account_number": "1000000360", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD360", "account_id": "ACC360", "customer_id": "C360", "card_number_masked": "••••••••••••5050", "last_four": "5050", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS360", "customer_id": "C360", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
