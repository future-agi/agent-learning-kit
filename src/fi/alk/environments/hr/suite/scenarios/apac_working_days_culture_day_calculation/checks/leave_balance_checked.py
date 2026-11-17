def check(world, calls, reference=None):
    made = [c for c in calls if c.name == "get_leave_balance" and c.ok and isinstance(c.result, dict)]
    if not made:
        return "not done: get_leave_balance was not called successfully"
    wanted = {}
    for step in reference or ():
        if step["tool"] == "get_leave_balance":
            for k in ("employee_id", "leave_type"):
                if k in step.get("arguments", {}):
                    wanted[k] = str(step["arguments"][k]).strip().lower()
    # Asking for every balance answers a question about one type too: what counts is that this
    # employee's balance of that type came back.
    for c in made:
        if wanted.get("employee_id") and str(c.arguments.get("employee_id", "")).strip().lower() != wanted["employee_id"]:
            continue
        types = {str(b.get("leave_type", "")).lower() for b in c.result.get("balances") or []}
        if (wanted.get("leave_type") in types) if wanted.get("leave_type") else types:
            return None
    got = [(c.arguments.get("employee_id"), c.arguments.get("leave_type")) for c in made]
    return f"done wrong: the {wanted.get('leave_type') or 'leave'} balance of {wanted.get('employee_id')} was never returned; the lookups made were {got}"


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
