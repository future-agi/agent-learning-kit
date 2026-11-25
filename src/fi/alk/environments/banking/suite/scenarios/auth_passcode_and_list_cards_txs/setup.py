def setup(world):
    world.put("customers", {"customer_id": "C118", "first_name": "Marie", "last_name": "Dubois", "email": "marie.d@example.ca", "phone": "+15145550118", "passcode": "6118", "kba_answer": "Quebec", "otp_code": "391024", "address": "118 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC118", "customer_id": "C118", "account_number": "1000000118", "account_type": "checking", "balance": 480000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD118", "account_id": "ACC118", "customer_id": "C118", "card_number_masked": "••••••••••••6118", "last_four": "6118", "card_type": "debit", "status": "active", "expiration_date": "2027-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD118", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("transactions", {"transaction_id": "TX118", "account_id": "ACC118", "card_id": "CARD118", "merchant_name": "Pharmacie Santé", "amount": 3200, "currency": "USD", "status": "posted", "transaction_time": "2026-10-24T09:00:00Z", "description": "Pharmacy goods"})
    world.put("sessions", {"session_id": "SESS118", "customer_id": "C118", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:50:00Z"})
    world.change("session_caller", "default", {"customer_id": "C118", "staff_id": None}, by="id")
