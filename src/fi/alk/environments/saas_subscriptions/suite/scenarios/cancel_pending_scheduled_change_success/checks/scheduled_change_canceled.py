def check(world, calls):
    cancel_calls = [c for c in calls if c.name == "cancel_scheduled_change" and c.ok]
    if not cancel_calls:
        return "not done: cancel_scheduled_change was not called successfully"
    canceled_records = [r for r in world.state().get("scheduled_changes", []) if r.get("status") == "canceled"]
    if not canceled_records:
        return "not done: no scheduled change was marked canceled in world state"
    for c in cancel_calls:
        cid = c.arguments.get("change_id")
        tid = c.arguments.get("tenant_id")
        match = [r for r in canceled_records if r.get("change_id") == cid and r.get("tenant_id") == tid]
        if not match:
            return f"done wrong: change {cid!r} for tenant {tid!r} is not marked canceled in world state"
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
