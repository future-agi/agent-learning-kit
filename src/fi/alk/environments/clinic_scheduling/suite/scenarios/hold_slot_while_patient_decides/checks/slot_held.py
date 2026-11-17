def check(world, calls, reference=None):
    want = {}
    for step in reference or ():
        if step.get("tool") == "hold_slot":
            want = {k: v for k, v in step.get("arguments", {}).items() if k in ("patient_id", "slot_id")}
    state = world.state()
    now = (state.get("clock") or [{"now": ""}])[0]["now"]
    new = [h for h in state.get("slot_holds", []) if h["created_at"] == now and h["status"] == "active"]
    if not new:
        return "not done: no hold was placed"
    if want and not any(all(h.get(k) == v for k, v in want.items()) for h in new):
        return f"done wrong: the hold is on {[h['slot_id'] for h in new]}, not {want.get('slot_id')}"
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
