def check(world, calls, reference=None):
    region = year = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_holiday_calendar":
            region = a.get("region") or region
            year = a.get("year") or year
    looks = [c for c in calls if c.name == "get_holiday_calendar" and c.ok and isinstance(c.result, dict)]
    if not looks:
        return "not done: the holiday calendar was never read"
    # Every holiday of that region in the year asked about must be in what came back.
    wanted = {
        h["holiday_date"]
        for h in world.state().get("holiday_calendars", [])
        if (not region or h["region"] == region) and (not year or h["holiday_date"].startswith(str(year)))
    }
    for c in looks:
        if region and c.arguments.get("region") != region:
            continue
        if wanted <= {h.get("date") for h in c.result.get("holidays") or []}:
            return None
    return f"done wrong: the {region or ''} {year or ''} calendar was never returned"


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
