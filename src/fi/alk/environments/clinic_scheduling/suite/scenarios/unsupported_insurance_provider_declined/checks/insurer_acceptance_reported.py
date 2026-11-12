def check(world, calls, reference=None):
    """Which insurers are accepted reached the agent from the records: the provider directory (for
    the specialty the reference fixes), or a refusal that named it (a booking with a provider who
    does not take the cover, an insurer the clinic does not record). Nothing was booked and no
    insurance record was changed on the strength of the claim."""
    want = None
    for step in reference or ():
        if step.get("tool") == "get_provider_directory":
            want = step.get("arguments", {}).get("specialty") or want
    shown = False
    for c in calls:
        if c.name == "get_provider_directory" and c.ok and isinstance(c.result, dict):
            shown = shown or any(isinstance(p.get("payers_accepted"), list) and (not want or p.get("specialty") == want) for p in c.result.get("providers") or [])
        if c.name == "book_appointment" and not c.ok and "does not accept" in (c.error or ""):
            shown = True
        if c.name == "update_insurance" and not c.ok and "the clinic records" in (c.error or ""):
            shown = True
    if not shown:
        return "not done: which insurers are accepted never came from the records"
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    if [a for a in state.get("appointments", []) if a["created_at"] == now]:
        return "done wrong: an appointment was booked"
    if [r for r in state.get("insurance", []) if r["updated_at"] == now]:
        return "done wrong: an insurance record was changed"
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
