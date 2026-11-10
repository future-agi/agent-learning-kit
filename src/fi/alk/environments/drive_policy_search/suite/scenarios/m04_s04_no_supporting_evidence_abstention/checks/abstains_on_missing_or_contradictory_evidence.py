def check(world, calls, reference):
    expected_queries = []
    for step in reference:
        if step.get("tool") == "search_files":
            q = step.get("arguments", {}).get("query")
            if q:
                expected_queries.append(q.lower())
    searches = [c for c in calls if c.name == "search_files" and c.ok]
    if not searches:
        return "not done: agent made no search tool call"
    if expected_queries:
        matched = False
        for c in searches:
            q_arg = str(c.arguments.get("query", "")).lower()
            if any(eq in q_arg or q_arg in eq or any(w in q_arg for w in eq.split()) for eq in expected_queries):
                matched = True
                break
        if not matched:
            return f"done wrong: searches did not query expected topic {expected_queries!r}"
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
