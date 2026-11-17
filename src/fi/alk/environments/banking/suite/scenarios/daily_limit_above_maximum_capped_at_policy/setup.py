def setup(world):
    world.put("customers", {"customer_id": "C310", "first_name": "Samuel", "last_name": "Osei", "email": "samuel.osei@example.com", "phone": "+1555123310", "passcode": "8812", "kba_answer": "Pepper", "otp_code": "650937", "address": "310 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC310", "customer_id": "C310", "account_number": "1000000310", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD310", "account_id": "ACC310", "customer_id": "C310", "card_number_masked": "••••••••••••3307", "last_four": "3307", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD310", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS310", "customer_id": "C310", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
