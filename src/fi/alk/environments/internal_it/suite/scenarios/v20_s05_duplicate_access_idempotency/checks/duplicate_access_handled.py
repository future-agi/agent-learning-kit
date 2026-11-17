def check(world, calls):
    access_checks = [c for c in calls if c.name == "get_current_access" and c.ok and c.arguments.get("employee_id")]
    if not access_checks:
        return "not done: get_current_access was not called with employee_id to inspect existing entitlements"

    bad_calls = [c for c in calls if c.name in ("create_access_request", "grant_synthetic_access") and c.ok]
    if bad_calls:
        return "done wrong: created duplicate request or grant when active access was already present"

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
