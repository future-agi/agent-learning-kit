def check(world, calls, reference=None):
    # The order the customer asks about: the one the reference fixes, or else the newest order of
    # the customer the reference identifies by email; its status as a successful read returned it.
    state = world.state()
    wanted = None
    email = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") in ("get_order", "get_shipment_tracking") and a.get("order_id"):
            wanted = a["order_id"]
        if step.get("tool") == "search_orders" and a.get("email"):
            email = a["email"].strip().lower()
    if not wanted and email:
        cust = next((c for c in state.get("customers", []) if c["email"].lower() == email), None)
        mine = sorted((o for o in state.get("orders", []) if cust and o["customer_id"] == cust["customer_id"]), key=lambda o: o["order_date"])
        wanted = mine[-1]["order_id"] if mine else None
    if not wanted:
        return "not done: the reference identifies no order"

    def shows(c):
        if not c.ok or not isinstance(c.result, dict):
            return False
        if c.name == "search_orders":
            return any(o.get("order_id") == wanted and o.get("status") for o in c.result.get("orders") or [])
        if c.name in ("get_order", "get_shipment_tracking"):
            return c.result.get("order_id") == wanted and bool(c.result.get("status") or c.result.get("order_status"))
        return False

    if not any(shows(c) for c in calls):
        return f"not done: the status of order {wanted} was never read"
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
