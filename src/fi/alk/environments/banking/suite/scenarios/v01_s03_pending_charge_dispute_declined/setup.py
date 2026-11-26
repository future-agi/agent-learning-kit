def setup(world):
    world.put("customers", {"customer_id": "C103", "first_name": "Charlie", "last_name": "Pemberton", "email": "charlie.pemberton@example.com", "phone": "+15551234103", "passcode": "3617", "kba_answer": "Max", "otp_code": "374829", "address": "103 Example Street, Springfield, IL 62701", "customer_since": "2021-04-12"})
    world.put("accounts", {"account_id": "ACC103", "customer_id": "C103", "account_number": "1000000103", "account_type": "checking", "balance": 420000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD103", "account_id": "ACC103", "customer_id": "C103", "card_number_masked": "••••••••••••8254", "last_four": "8254", "card_type": "debit", "status": "active", "expiration_date": "2027-11-30", "locked_at": None})
    world.put("card_controls", {"card_id": "CARD103", "international_enabled": 0, "online_enabled": 1, "contactless_enabled": 1, "daily_limit": 250000, "updated_at": "2026-01-15T09:00:00Z"})
    world.put("sessions", {"session_id": "SESS103", "customer_id": "C103", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    world.put("transactions", {"transaction_id": "TX103", "account_id": "ACC103", "card_id": "CARD103", "merchant_name": "Corner Cafe", "amount": 2000, "currency": "USD", "status": "pending", "transaction_time": "2026-10-26T09:15:00Z", "description": "Pending coffee order"})
    world.change("session_caller", "default", {"customer_id": "C103", "staff_id": None}, by="id")
