def check(world, calls, reference=None):
    team = None
    for step in reference or ():
        if step.get("tool") == "escalate_to_staff":
            team = step.get("arguments", {}).get("team") or team
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    esc = [e for e in state.get("escalations", []) if e["created_at"] == now]
    if not esc:
        return "not done: no escalation was created"
    if team and not any(e["team"] == team for e in esc):
        return f"done wrong: escalated to {[e['team'] for e in esc]}, the case needs {team}"
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
