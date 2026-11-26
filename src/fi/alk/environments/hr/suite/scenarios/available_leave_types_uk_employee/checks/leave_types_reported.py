def check(world, calls, reference=None):
    emp = None
    for step in reference or ():
        if step.get("tool") == "get_leave_types" and step.get("arguments", {}).get("employee_id"):
            emp = step["arguments"]["employee_id"]
    looks = [c for c in calls if c.name == "get_leave_types" and c.ok and isinstance(c.result, dict)]
    if not looks:
        return "not done: the leave types the employee may request were never read"
    # The list must be the employee's own: their region decides which policy governs each type, and
    # the balance each one draws on is theirs.
    state = world.state()
    region = next((e["region"] for e in state.get("employees", []) if e["employee_id"] == emp), None)
    for c in looks:
        if emp and c.arguments.get("employee_id") != emp:
            continue
        if c.result.get("leave_types") and (not region or c.result.get("region") == region):
            return None
    return f"done wrong: the leave types of {emp} were never returned"


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
