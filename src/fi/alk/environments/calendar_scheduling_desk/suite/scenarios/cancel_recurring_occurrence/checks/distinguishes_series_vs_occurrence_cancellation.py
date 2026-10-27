def check(world, calls):
    del_calls = [c for c in calls if c.name == "delete_event" and c.ok]
    if not del_calls:
        return "not done: delete_event was not called"
    this_event_calls = [c for c in del_calls if (c.arguments or {}).get("recurring_action", "this_event") == "this_event"]
    if not this_event_calls:
        return "done wrong: delete_event recurring_action was not this_event"
    st = world.state()
    evts = st.get("events", [])
    cancelled = [e for e in evts if e.get("status") == "cancelled"]
    if not cancelled:
        return "not done: target event was not marked cancelled in world state"
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
