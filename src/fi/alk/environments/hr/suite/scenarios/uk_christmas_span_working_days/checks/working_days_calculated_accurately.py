def check(world, calls, reference=None):
    made = [c for c in calls if c.name == "calculate_working_days" and c.ok and isinstance(c.result, dict)]
    if not made:
        return "not done: calculate_working_days was not called successfully"
    wanted = {}
    for step in reference or ():
        if step["tool"] == "calculate_working_days":
            for k in ("region", "start_date", "end_date"):
                if k in step.get("arguments", {}):
                    wanted[k] = str(step["arguments"][k]).strip().lower()
    # One calculation must cover the whole question: region and both dates from the same call, and
    # its answer (the working-day count) returned to the agent.
    for c in made:
        if all(str(c.arguments.get(k, "")).strip().lower() == v for k, v in wanted.items()) and "working_days" in c.result:
            return None
    got = [{k: c.arguments.get(k) for k in ("region", "start_date", "end_date")} for c in made]
    return f"done wrong: no single calculation of {wanted}; the calculations made were {got}"


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
