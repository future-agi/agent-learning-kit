def check(world, calls, reference=None):
    made = [c for c in calls if c.name == "search_hr_policy" and c.ok and isinstance(c.result, dict)]
    if not made:
        return "not done: search_hr_policy was not called successfully"
    wanted = {}
    for step in reference or ():
        if step["tool"] == "search_hr_policy":
            for k in ("region", "topic"):
                if k in step.get("arguments", {}):
                    wanted[k] = str(step["arguments"][k]).strip().lower()
    state = world.state()
    today = (state.get("clock") or [{"now": "2026-10-26T09:00:00Z"}])[0]["now"][:10]
    # The policy in force today for that region and topic must be in what the search returned: a
    # search on another date can return only a superseded version, or nothing.
    in_force = {
        p["policy_id"]
        for p in state.get("hr_policies", [])
        if p["effective_from"] <= today <= p["effective_to"]
        and (not wanted.get("topic") or p["topic"].lower() == wanted["topic"])
        and (not wanted.get("region") or p["region"].lower() in (wanted["region"], "all"))
    }
    for c in made:
        if all(str(c.arguments.get(k, "")).strip().lower() == v for k, v in wanted.items()):
            shown = {p.get("policy_id") for p in c.result.get("policies") or [] if p.get("content")}
            if shown & in_force:
                return None
    got = [{k: c.arguments.get(k) for k in ("region", "topic", "effective_date")} for c in made]
    return f"done wrong: no search returned the policy in force for {wanted} ({sorted(in_force)}); the searches made were {got}"


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
