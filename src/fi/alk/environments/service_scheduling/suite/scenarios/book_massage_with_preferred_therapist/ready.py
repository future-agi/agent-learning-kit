def ready(world):
    s = world.state()
    cust = next((c for c in s.get("customers", []) if c["customer_id"] == "CU402"), None)
    if not cust:
        return "customer CU402 missing"
    pref = next((p for p in s.get("customer_preferences", []) if p["customer_id"] == "CU402" and p["kind"] == "staff"), None)
    if not pref or pref["value"] != "ST01":
        return "customer CU402 preferred staff ST01 missing"
    return None
