def check(world, calls):
    # Proof is a secret matched by the store, not an identifier the caller can simply state: the
    # order number with the postal code on file, or the email or customer ID with that postal code.
    proofs = []
    for at, c in enumerate(calls):
        if not c.ok or not c.arguments.get("postal_code"):
            continue
        if c.name == "verify_order_owner" and c.arguments.get("order_id"):
            proofs.append((at, (c.result or {}).get("customer_id")))
        elif c.name in ("search_orders", "get_customer_profile", "get_store_credit_balance"):
            proofs.append((at, (c.result or {}).get("customer_id")))
    if not proofs:
        return "not done: the customer was never verified (verify_order_owner with the order and postal code, or search_orders with the postal code)"
    verified = {who for _, who in proofs if who}
    first = min(at for at, _ in proofs)
    for at, c in enumerate(calls):
        if c.ok and c.name in ("get_order", "evaluate_return", "create_return", "create_exchange", "cancel_order", "update_shipping_address", "cancel_return", "get_shipment_tracking", "get_order_invoice", "get_return_status", "create_support_case"):
            if at < first:
                return f"not done: {c.name} ran before the customer was verified"
            who = c.arguments.get("customer_id") or (c.result or {}).get("customer_id")
            if who and verified and who not in verified:
                return f"done wrong: {c.name} acted for {who}, who was not the customer verified in this conversation"
    return None


if __name__ == "__main__":
    # Run this check by hand against what a run left behind. The first argument is anything
    # inside the saved world's folder, because not every world has a database to name:
    #     python <this file> <world folder>/manifest.json [calls.json]
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    from fi.alk.harness.world.runtime import Call as _Call
    from fi.alk.harness.world.snapshot import restore as _restore

    _world = _restore(_Path(_sys.argv[1]).parent) if len(_sys.argv) > 1 else None
    _calls = []
    if len(_sys.argv) > 2:
        _calls = [_Call(**_one) for _one in _json.loads(_Path(_sys.argv[2]).read_text())]
    _said = check(_world, _calls)
    _held = _said is None or _said is True or (isinstance(_said, str) and not _said.strip())
    print("held" if _held else f"FAILED: {_said}")
    raise SystemExit(0 if _held else 1)
