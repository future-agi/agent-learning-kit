def check(world, calls, reference):
    # The order under the duplicate attempt, which the reference fixes; what the agent was shown of
    # its returns (status, an evaluation, its lines, or the store refusing a second return), and
    # that no further return was authorized on it.
    wanted = {s["arguments"]["order_id"] for s in reference or () if s["tool"] in ("evaluate_return", "get_return_status", "get_order") and s.get("arguments", {}).get("order_id")}

    def on_order(c):
        if not isinstance(c.result, dict):
            return set()
        if c.name == "get_return_status":
            return {r.get("order_id") for r in c.result.get("returns") or []}
        return {c.result.get("order_id")}

    shown = [
        c for c in calls
        if (c.ok and c.name in ("evaluate_return", "get_return_status", "get_order") and (on_order(c) & wanted if wanted else on_order(c)))
        or (not c.ok and c.name == "create_return" and str(c.error or "").startswith("conflict") and (not wanted or c.arguments.get("order_id") in wanted))
    ]
    if not shown:
        return f"not done: the returns already on order {', '.join(sorted(wanted)) or 'in question'} were never looked up"
    created = [c for c in calls if c.name == "create_return" and c.ok and (not wanted or c.arguments.get("order_id") in wanted)]
    if created:
        return f"done wrong: another return was created on {created[0].arguments.get('order_id')}, whose units were already returned"
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
