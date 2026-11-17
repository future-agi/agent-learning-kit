def check(world, calls, reference=None):
    emp = days = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_approval_chain" and a.get("employee_id"):
            emp = a["employee_id"]
            days = a.get("working_days", days)
    looks = [c for c in calls if c.name == "get_approval_chain" and c.ok and isinstance(c.result, dict)]
    if not looks:
        return "not done: the approval chain was never read"
    # The chain must be asked for the length the employee named: above 10 working days the manager's
    # manager approves too, and the chain says so only when asked for that length.
    need = 2 if isinstance(days, (int, float)) and days > 10 else 1
    for c in looks:
        if emp and c.arguments.get("employee_id") != emp:
            continue
        if days is not None and c.arguments.get("working_days") != days:
            continue
        if len(c.result.get("approvers") or []) >= need:
            return None
    return f"done wrong: {emp}'s approval chain for {days or 'this'} working days was never returned"


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
