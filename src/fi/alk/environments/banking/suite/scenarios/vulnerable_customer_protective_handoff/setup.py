def setup(world):
    world.put("customers", {"customer_id": "C111", "first_name": "Lakshmi", "last_name": "Patel", "email": "lakshmi.patel@example.in", "phone": "+919876543111", "passcode": "1952", "kba_answer": "Mumbai", "otp_code": "418295"})
    world.put("accounts", {"account_id": "ACC111", "customer_id": "C111", "account_number": "1000000111", "account_type": "checking", "balance": 180000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD111", "account_id": "ACC111", "customer_id": "C111", "card_number_masked": "••••••••••••5511", "last_four": "5511", "card_type": "debit", "status": "active", "expiration_date": "2027-10-31", "locked_at": None})
    world.put("transactions", {"transaction_id": "TX111", "account_id": "ACC111", "card_id": "CARD111", "merchant_name": "QuickTransfer Web", "amount": 4500, "currency": "USD", "status": "posted", "transaction_time": "2026-10-24T14:30:00Z", "description": "QuickTransfer Web charge"})
    world.put("sessions", {"session_id": "SESS111", "customer_id": "C111", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T12:15:00Z"})
