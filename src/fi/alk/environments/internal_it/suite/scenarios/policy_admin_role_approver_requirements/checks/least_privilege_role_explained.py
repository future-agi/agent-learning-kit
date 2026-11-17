def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == "check_access_policy":
            want = step.get("arguments", {})
    looks = [c for c in calls if c.name in ("check_access_policy", "get_role_details") and c.ok]
    if not looks:
        return "not done: neither the roles nor the policy check were read"
    if want.get("role_name") and not any(c.name == "check_access_policy" and c.arguments.get("role_name") == want["role_name"] and c.arguments.get("app_id") == want.get("app_id") for c in looks):
        return f"done wrong: the policy was not checked for {want.get('app_id')} {want['role_name']}"
    if any(c.name in ("create_access_request", "grant_synthetic_access") and c.ok for c in calls):
        return "done wrong: something was requested or granted in a question-only scenario"
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
