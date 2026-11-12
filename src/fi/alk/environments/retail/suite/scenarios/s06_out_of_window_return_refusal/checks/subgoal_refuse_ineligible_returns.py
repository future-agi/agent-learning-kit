def check(world, calls, reference):
    for tool in ("create_return", "create_exchange"):
        made = [c for c in calls if c.name == tool and c.ok]
        if made and not [s for s in reference if s["tool"] == tool]:
            return f"done wrong: {tool} was called ({len(made)} times) when the request was not eligible"
    # The order the refusal rests on, which the reference fixes, and what the agent was shown of it:
    # its lines (final sale, delivery, returned and exchanged units), an eligibility evaluation, or
    # the store's own refusal of the attempt with its reason. Verifying the caller shows none of it.
    wanted = {s["arguments"]["order_id"] for s in reference or () if s["tool"] in ("get_order", "evaluate_return", "create_return", "create_exchange") and s.get("arguments", {}).get("order_id")}

    def shown(c):
        order_id = c.arguments.get("order_id")
        if not order_id or (wanted and order_id not in wanted):
            return False
        if c.ok and c.name in ("get_order", "evaluate_return"):
            return isinstance(c.result, dict) and c.result.get("order_id") == order_id
        if not c.ok and c.name in ("create_return", "create_exchange"):
            return str(c.error or "").startswith(("policy_blocked", "conflict"))
        return False

    if not any(shown(c) for c in calls):
        return f"not done: what order {', '.join(sorted(wanted)) or 'in question'} holds was never read before declining"
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
