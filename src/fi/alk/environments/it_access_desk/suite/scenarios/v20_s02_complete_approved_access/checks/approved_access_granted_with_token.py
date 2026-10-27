def check(world, calls):
    made = [c for c in calls if c.name == "grant_synthetic_access" and c.ok]
    if not made:
        return "not done: grant_synthetic_access was not called successfully"

    state = world.state()
    ents = [e for e in state.get("entitlements", []) if e.get("status") == "active"]
    if not ents:
        return "not done: no active entitlement found in the world"

    requests = state.get("access_requests", [])
    completed_reqs = [r for r in requests if r.get("status") == "completed"]
    if not completed_reqs:
        return "done wrong: access request was not marked completed in the world"

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
