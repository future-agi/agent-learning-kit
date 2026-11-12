def check(world, calls, reference=None):
    """Each piece of clinic information the reference reads (a location, a specialty's preparation,
    the provider directory, with whatever it fixes) was returned to the agent with its content."""
    info = ("get_location_info", "get_preparation_instructions", "get_provider_directory")
    targets = [(step.get("tool"), step.get("arguments", {})) for step in reference or () if step.get("tool") in info]
    if not targets:
        return "not done: the reference fixes no clinic information to report"

    def shows(tool, fixed, result):
        if not isinstance(result, dict):
            return False
        if tool == "get_location_info":
            locations = result.get("locations") or []
            return bool(locations) and (not fixed.get("location_id") or any(l.get("location_id") == fixed["location_id"] for l in locations))
        if tool == "get_preparation_instructions":
            return bool(result.get("instructions")) and result.get("specialty") == fixed.get("specialty", result.get("specialty"))
        providers = result.get("providers") or []
        return any(
            (not fixed.get("specialty") or p.get("specialty") == fixed["specialty"])
            and (not fixed.get("language") or fixed["language"] in (p.get("languages") or []))
            and (not fixed.get("payer") or fixed["payer"] in (p.get("payers_accepted") or []))
            and (not fixed.get("location_id") or any(fixed["location_id"] in s for s in p.get("sessions") or []))
            for p in providers
        )

    for tool, fixed in targets:
        if not any(c.name == tool and c.ok and shows(tool, fixed, c.result) for c in calls):
            what = ", ".join(f"{k}={v}" for k, v in fixed.items()) or "the clinic"
            return f"not done: {tool} for {what} never returned its content"
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
