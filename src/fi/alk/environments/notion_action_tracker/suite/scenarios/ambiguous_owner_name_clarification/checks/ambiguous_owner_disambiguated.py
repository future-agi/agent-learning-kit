def check(world, calls, reference):
    lookups = [c for c in calls if c.name in ("notion_fetch_page", "notion_search", "notion_get_database_schema") and c.ok]
    if not lookups:
        return "not done: did not fetch meeting notes to inspect assignee details"
    ref_creates = [s for s in reference if s["tool"] == "notion_create_action_record"]
    if not ref_creates:
        guessed = [c for c in calls if c.name == "notion_create_action_record" and c.ok]
        if guessed:
            return "done wrong: created action record with a guessed owner without resolving ambiguity"
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
