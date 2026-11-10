def check(world, calls, reference):
    eval_calls = [c for c in calls if c.name in ("evaluate_return", "get_return_policy", "get_order", "verify_order_owner", "get_product") and c.ok]
    if not eval_calls:
        return "not done: agent did not evaluate return eligibility or check order/policy"
    for tool in ("create_return", "create_exchange"):
        made = [c for c in calls if c.name == tool and c.ok]
        if made and not [s for s in reference if s["tool"] == tool]:
            return f"done wrong: {tool} was called ({len(made)} times) when the request was not eligible"
    wanted = [(s["tool"], s["arguments"]["order_id"]) for s in reference if s["tool"] in ("evaluate_return", "get_order") and s.get("arguments", {}).get("order_id")]
    if wanted and not any((c.name, c.arguments.get("order_id")) in wanted for c in eval_calls):
        return f"done wrong: order {wanted[0][1]} was never evaluated before declining"
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
