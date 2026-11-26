def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "create_access_request" and a.get("replaces_entitlement_id"):
            want = a
    if not want:
        return "not done: the scenario fixes no role swap"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    old_id = want["replaces_entitlement_id"]
    old = next((e for e in state.get("entitlements", []) if e["entitlement_id"] == old_id), None)
    new = [
        r for r in state.get("access_requests", [])
        if r.get("created_at") == now and all(r.get(k) == want.get(k) for k in ("employee_id", "app_id", "role_name"))
    ]
    if not new:
        return f"not done: no request for {want.get('role_name')} on {want.get('app_id')} was raised in this run"
    if new[0].get("replaces_entitlement_id") != old_id:
        return f"done wrong: the {want.get('role_name')} request does not say it replaces {old_id}"
    # Until the new role is granted the employee keeps the old one: ending it first would leave
    # them with neither if the new request is refused.
    if new[0].get("status") != "completed" and (not old or old.get("status") != "active"):
        return f"done wrong: {old_id} was ended before its replacement was granted"
    if new[0].get("status") == "completed" and old and old.get("status") == "active":
        return f"done wrong: {old_id} is still active beside the {want.get('role_name')} role that replaced it"
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
    # Only the verdict: a failure names the records it read, so it is not written to the console.
    print("held" if _held else "FAILED")
    raise SystemExit(0 if _held else 1)
