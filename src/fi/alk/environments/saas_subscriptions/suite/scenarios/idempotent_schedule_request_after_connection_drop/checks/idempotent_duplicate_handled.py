def check(world, calls):
    sched_calls = [c for c in calls if c.name == "schedule_plan_change" and c.ok]
    if not sched_calls:
        return "not done: schedule_plan_change was not called"
    
    pending = [r for r in world.state().get("scheduled_changes", []) if r.get("status") == "pending"]
    if not pending:
        return "not done: no pending scheduled change found in world state"
    
    for c in sched_calls:
        tid = c.arguments.get("tenant_id")
        t_pending = [r for r in pending if r.get("tenant_id") == tid]
        if len(t_pending) > 1:
            return f"done wrong: duplicate pending changes created for tenant {tid!r}"
        if len(t_pending) == 1:
            sc = t_pending[0]
            if c.arguments.get("target_plan_id") and sc.get("target_plan_id") != c.arguments.get("target_plan_id"):
                return f"done wrong: target_plan_id in world ({sc.get('target_plan_id')!r}) does not match call ({c.arguments.get('target_plan_id')!r})"
            if c.arguments.get("target_seats") is not None and sc.get("target_seats") != c.arguments.get("target_seats"):
                return f"done wrong: target_seats in world ({sc.get('target_seats')}) does not match call ({c.arguments.get('target_seats')})"
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
