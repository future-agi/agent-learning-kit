def check(world, calls):
    prev_calls = [c for c in calls if c.name == "preview_plan_change" and c.ok]
    if not prev_calls:
        return "not done: preview_plan_change was not called successfully"
    for c in prev_calls:
        if not c.arguments.get("tenant_id") or not c.arguments.get("target_plan_id") or c.arguments.get("target_seats") is None:
            return "done wrong: preview_plan_change called with missing required parameters"
    mutations = [c for c in calls if c.name == "schedule_plan_change" and c.ok]
    if mutations:
        return "done wrong: schedule_plan_change was called when user asked only for preview"
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
