def check(world, calls, reference=None):
    state = world.state()
    actions = state.get("action_records", [])
    completed_or_updated = any(a.get("status") in ("Completed", "Done") for a in actions)
    if not completed_or_updated:
        return "not done: expected updated action record with status 'Completed' in database"
    if reference:
        ref_updates = [s["arguments"] for s in reference if s.get("tool") == "notion_update_action_record"]
        for req in ref_updates:
            action_id = req.get("action_id")
            exp_status = req.get("status")
            matched = any(a.get("action_id") == action_id and (not exp_status or a.get("status") == exp_status) for a in actions)
            if not matched:
                return f"not done: expected action {action_id!r} with status {exp_status!r} in database"
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
