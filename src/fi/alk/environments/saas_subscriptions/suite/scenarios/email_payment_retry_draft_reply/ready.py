def ready(world):
    threads = {t["id"]: t for t in world.state().get("gmail_threads", [])}
    if "th_b14_retry_payment" not in threads:
        return "th_b14_retry_payment missing from gmail_threads"
    messages = {m["id"]: m for m in world.state().get("gmail_messages", [])}
    if "bmsg_018" not in messages:
        return "bmsg_018 missing from gmail_messages"
    payments = {p["invoice_id"]: p for p in world.state().get("payments", [])}
    if "inv_11_addon" not in payments:
        return "inv_11_addon missing from payments"
    return None
