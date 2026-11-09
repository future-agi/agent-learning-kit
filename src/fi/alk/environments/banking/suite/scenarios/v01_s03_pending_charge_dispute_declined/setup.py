def setup(world):
    world.put("customers", {"customer_id": "C103", "first_name": "Charlie", "last_name": "Johnson", "email": "charlie.j@example.com", "phone": "+15551234103", "passcode": "2948", "kba_answer": "Max", "otp_code": "374829"})
    world.put("accounts", {"account_id": "ACC103", "customer_id": "C103", "account_number": "1000000103", "account_type": "checking", "balance": 420000, "currency": "USD", "status": "active"})
    world.put("cards", {"card_id": "CARD103", "account_id": "ACC103", "customer_id": "C103", "card_number_masked": "••••••••••••3345", "last_four": "3345", "card_type": "debit", "status": "active", "expiration_date": "2027-11-30", "locked_at": None})
    world.put("sessions", {"session_id": "SESS103", "customer_id": "C103", "channel": "chat", "is_verified": False, "verified_at": None, "failed_attempts": 0, "created_at": "2026-10-26T11:50:00Z"})
    world.put("transactions", {"transaction_id": "TX103", "account_id": "ACC103", "card_id": "CARD103", "merchant_name": "Corner Cafe", "amount": 2000, "currency": "USD", "status": "pending", "transaction_time": "2026-10-26T09:15:00Z", "description": "Pending coffee order"})
