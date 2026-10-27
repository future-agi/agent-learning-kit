def ready(world):
    st = world.state()
    ledger = [r for r in st.get("message_ledger", []) if r.get("request_key") == "REQ-TIMEOUT-501"]
    if not ledger:
        return "expected message_ledger entry for REQ-TIMEOUT-501"
    return None
