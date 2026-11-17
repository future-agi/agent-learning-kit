def ready(world):
    threads = {t["id"]: t for t in world.state().get("gmail_threads", [])}
    if "th_b14_retry_payment" not in threads:
        return "th_b14_retry_payment missing from gmail_threads"
    payments = {p["invoice_id"]: p for p in world.state().get("payments", [])}
    if "inv_11_addon" not in payments:
        return "inv_11_addon missing from payments"
    return None
