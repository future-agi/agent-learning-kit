def check(world, calls, reference=None):
    state = world.state()
    cases = state.get("hr_cases", [])
    if not cases:
        return "not done: no HR case was recorded in the world"
        
    wanted = {}
    if reference:
        for step in reference:
            if step["tool"] == "create_hr_case":
                for k in ("employee_id", "category"):
                    if k in step.get("arguments", {}):
                        wanted[k] = step["arguments"][k]
                        
    for c in cases:
        matches = True
        for field, expected in wanted.items():
            if str(c.get(field, "")).strip().lower() != str(expected).strip().lower():
                matches = False
                break
        if matches:
            return None
            
    return f"done wrong: no recorded HR case matched expected fields {wanted}"


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
