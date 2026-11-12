def check(world, calls, reference=None):
    mgr = start = end = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_team_absences":
            mgr = a.get("manager_id") or mgr
            start = a.get("start_date") or start
            end = a.get("end_date") or end
    import re

    def day(value):
        value = str(value or "")
        return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None

    looks = [c for c in calls if c.name == "get_team_absences" and c.ok and isinstance(c.result, dict) and "absences" in c.result]
    if not looks:
        return "not done: the team view was never read"
    # The view must be the manager's own team over the whole period asked about: a view of other
    # dates returns absences that answer nothing.
    for c in looks:
        if mgr and c.arguments.get("manager_id") != mgr:
            continue
        got_start, got_end = day(c.arguments.get("start_date")), day(c.arguments.get("end_date"))
        if start and (not got_start or not day(start) or got_start > start):
            continue
        if end and (not got_end or not day(end) or got_end < end):
            continue
        return None
    return f"done wrong: the team view of {mgr} from {start} to {end} was never read"


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
