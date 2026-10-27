def check(world, calls, reference):
    successful_calls = [c for c in calls if c.ok]
    if not successful_calls:
        return "not done: no successful calendar inspection call was made before responding"
    st = world.state()
    evts = st.get("events", [])
    ambiguous_starts = ["2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00", "2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"]
    for e in evts:
        if e.get("start_time") in ambiguous_starts:
            return f"done wrong: event {e.get('event_id')} was created at ambiguous DST time without clarification"
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
