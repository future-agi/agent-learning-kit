def check(world, calls, reference=None):
    want = None
    for step in reference or ():
        if step.get("tool") == "verify_employee" and step.get("arguments", {}).get("employee_id"):
            want = step["arguments"]["employee_id"]
    if not want:
        return "not done: the scenario fixes no employee to verify"
    seen = next(
        (i for i, c in enumerate(calls)
         if c.name == "verify_employee" and c.ok and isinstance(c.result, dict) and c.result.get("employee_id") == want),
        None,
    )
    if seen is None:
        return f"not done: {want}'s directory record was never returned"
    acting = {"create_access_request", "request_temporary_access", "grant_synthetic_access", "revoke_synthetic_access",
              "create_it_handoff", "cancel_access_request", "record_approval_decision"}
    acted = 0
    for i, c in enumerate(calls):
        if c.name in acting and c.ok:
            acted += 1
            if i < seen:
                return f"done wrong: {c.name} ran before {want} was verified"
            target = c.arguments.get("employee_id")
            if target and target != want:
                return f"done wrong: {c.name} acted for {target}, not the verified {want}"
    if acted:
        # What was done after verifying must be in the access system's own record, not only claimed.
        state = world.state()
        now = (state.get("clock") or [{"now": ""}])[0]["now"]
        logged = [l for l in state.get("audit_log", []) if l.get("timestamp") == now]
        if len(logged) < acted:
            return f"not done: {acted - len(logged)} of the actions taken after verifying {want} left no record in the access system"
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
