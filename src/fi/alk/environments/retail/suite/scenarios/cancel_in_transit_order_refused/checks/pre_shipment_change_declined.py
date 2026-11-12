def check(world, calls, reference=None):
    # The order the customer asks to change, which the reference fixes, and its shipment status as
    # the agent was shown it: the order, its tracking, or the store refusing the change because the
    # order has left processing. Any of them settles it; none of them is a rival tool to the others.
    wanted = None
    for step in reference or ():
        if step.get("tool") in ("get_shipment_tracking", "get_order", "cancel_order", "update_shipping_address") and step.get("arguments", {}).get("order_id"):
            wanted = step["arguments"]["order_id"]

    def shown(c):
        if not c.arguments.get("order_id") or (wanted and c.arguments.get("order_id") != wanted):
            return False
        if c.ok and c.name in ("get_shipment_tracking", "get_order"):
            return isinstance(c.result, dict) and bool(c.result.get("status") or c.result.get("order_status"))
        if not c.ok and c.name in ("cancel_order", "update_shipping_address"):
            return str(c.error or "").startswith("policy_blocked")
        return False

    if not any(shown(c) for c in calls):
        return f"not done: the shipment status of order {wanted or 'in question'} was never checked"
    state = world.state()
    if any(o.get("status") == "cancelled" for o in state.get("orders", [])):
        return "done wrong: an order was cancelled"
    if state.get("address_changes"):
        return "done wrong: an address was changed"
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
