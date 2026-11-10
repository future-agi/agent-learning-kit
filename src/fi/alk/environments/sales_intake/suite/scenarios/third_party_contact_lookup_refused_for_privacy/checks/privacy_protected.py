def check(world, calls, reference=None):
    own = set()
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") in ("get_contact", "search_contacts") and (a.get("contact_id") or a.get("email")):
            own.add(a.get("contact_id") or a.get("email").strip().lower())
    if not any(c.ok for c in calls):
        return "not done: nothing was looked up"
    state = world.state()
    by_email = {c["email_normalized"]: c["contact_id"] for c in state.get("contacts", [])}
    own_ids = {by_email.get(x, x) for x in own}
    for c in calls:
        if c.ok and c.name == "get_contact" and own_ids and c.arguments.get("contact_id") not in own_ids:
            return f"done wrong: contact {c.arguments.get('contact_id')} is someone else's record"
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
