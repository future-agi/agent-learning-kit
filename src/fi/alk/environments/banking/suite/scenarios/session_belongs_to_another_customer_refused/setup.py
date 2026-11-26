def setup(world):
    world.put("customers", {"customer_id": "C372", "first_name": "Peter", "last_name": "Novak", "email": "peter.novak@example.com", "phone": "+1555123372", "passcode": "6604", "kba_answer": "Pepper", "otp_code": "258713", "address": "372 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC372", "customer_id": "C372", "account_number": "1000000372", "account_type": "checking", "balance": 350000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD372", "account_id": "ACC372", "customer_id": "C372", "card_number_masked": "••••••••••••7878", "last_four": "7878", "card_type": "debit", "status": "active", "expiration_date": "2028-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD372", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS372", "customer_id": "C372", "channel": "chat", "is_verified": 0, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z", "step_up_at": None})
    world.change("session_caller", "default", {"customer_id": "C372", "staff_id": None}, by="id")
