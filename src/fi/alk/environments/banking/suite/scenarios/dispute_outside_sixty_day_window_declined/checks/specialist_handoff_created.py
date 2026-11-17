def check(world, calls, reference=None):
    wanted = None
    for step in reference or ():
        if step.get("tool") == "create_handoff" and step.get("arguments", {}).get("customer_id"):
            wanted = step["arguments"]["customer_id"]
    handoff_calls = [c for c in calls if c.name == "create_handoff" and c.ok]
    if not handoff_calls:
        return "not done: create_handoff was not called"
    handoffs = world.state().get("handoffs", [])
    if not handoffs:
        return "not done: no handoff record found in database"
    for c in handoff_calls:
        cid = c.arguments.get("customer_id")
        if wanted and cid != wanted:
            return f"done wrong: the handoff was created for {cid}, not for this customer {wanted}"
        if not any(h.get("customer_id") == cid for h in handoffs):
            return f"not done: no handoff record in database for {cid}"
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
