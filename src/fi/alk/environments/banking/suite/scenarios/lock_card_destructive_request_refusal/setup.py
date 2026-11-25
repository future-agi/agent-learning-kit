def setup(world):
    world.put("customers", {"customer_id": "C112", "first_name": "Rachel", "last_name": "Tremblay", "email": "rachel.t@example.ca", "phone": "+15145550112", "passcode": "8812", "kba_answer": "Montreal", "otp_code": "729104", "address": "112 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC112", "customer_id": "C112", "account_number": "1000000112", "account_type": "checking", "balance": 750000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD112", "account_id": "ACC112", "customer_id": "C112", "card_number_masked": "••••••••••••8833", "last_four": "8833", "card_type": "debit", "status": "active", "expiration_date": "2027-12-31", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD112", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS112", "customer_id": "C112", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:20:00Z"})
    world.change("session_caller", "default", {"customer_id": "C112", "staff_id": None}, by="id")
