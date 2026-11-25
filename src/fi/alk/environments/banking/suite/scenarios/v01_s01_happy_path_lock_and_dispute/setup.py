def setup(world):
    world.put("customers", {"customer_id": "C101", "first_name": "Alice", "last_name": "Thornbury", "email": "alice.thornbury@example.com", "phone": "+15551234101", "passcode": "5826", "kba_answer": "Rover", "otp_code": "482903", "address": "101 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC101", "customer_id": "C101", "account_number": "1000000101", "account_type": "checking", "balance": 500000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD101", "account_id": "ACC101", "customer_id": "C101", "card_number_masked": "••••••••••••4762", "last_four": "4762", "card_type": "debit", "status": "active", "expiration_date": "2028-12-31", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD101", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS101", "customer_id": "C101", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    world.put("transactions", {"transaction_id": "TX101", "account_id": "ACC101", "card_id": "CARD101", "merchant_name": "Target Store #1042", "amount": 8420, "currency": "USD", "status": "posted", "transaction_time": "2026-10-24T14:22:10Z", "description": "In-store purchase"})
    world.change("session_caller", "default", {"customer_id": "C101", "staff_id": None}, by="id")
