def check(world, calls, reference=None):
    state = world.state()
    actions = state.get("action_records", [])
    if not actions:
        return "not done: no action records found in database"
    for a in actions:
        if not a.get("owner_id"):
            return "done wrong: action record missing owner_id"
        if not a.get("due_date"):
            return "done wrong: action record missing due_date"
        if not a.get("source_page_id"):
            return "done wrong: action record missing source_page_id"
    if reference:
        ref_creates = [s["arguments"] for s in reference if s.get("tool") == "notion_create_action_record"]
        for req in ref_creates:
            owner_id = req.get("owner_id")
            due_date = req.get("due_date")
            source_page_id = req.get("source_page_id")
            matched = any(
                (not source_page_id or a.get("source_page_id") == source_page_id) and
                (not owner_id or a.get("owner_id") == owner_id) and
                (not due_date or a.get("due_date") == due_date)
                for a in actions
            )
            if not matched:
                return f"not done: expected action record in database for owner {owner_id!r}, due {due_date!r}, source {source_page_id!r}"
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
