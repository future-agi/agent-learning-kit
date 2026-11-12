def check(world, calls, reference=None):
    want = None
    for step in reference or ():
        if step.get("tool") == "create_exchange":
            want = step.get("arguments", {})
    made = [c for c in calls if c.name == "create_exchange" and c.ok]
    if not made:
        return "not done: create_exchange was not called or did not succeed"
    rows = [e for e in world.state().get("exchanges", []) if e.get("status") != "cancelled"]
    if not rows:
        return "not done: no exchange record in database"
    if want:
        for key in ("order_id", "original_sku", "replacement_sku"):
            if want.get(key) and not any(e.get(key) == want[key] for e in rows):
                return f"done wrong: no exchange with {key} {want[key]}; the database holds {[(e['original_sku'], e['replacement_sku']) for e in rows]}"
        if len(rows) != 1:
            return f"done wrong: {len(rows)} exchanges exist, the customer asked for one"
        if want.get("quantity") and rows[0].get("quantity") != want["quantity"]:
            return f"done wrong: the exchange is for {rows[0].get('quantity')} unit(s), the customer asked for {want['quantity']}"
    if any(c.name == "create_return" and c.ok for c in calls):
        return "done wrong: a return was created as well as, or instead of, the exchange"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The evidence the reference fixes and whether its content reached this run: every message of an
    email thread (or the one email) with its body returned by a read, and a library document read.
    Reading a thread's metadata, or seeing it in a search, does not show what it says."""
    state = world.state()
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("email thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("email", a["messageId"]))
        elif step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted.append(("file", a["fileId"]))
    bodies = _bodies_shown(calls)
    missing = []
    for kind, target in wanted:
        if kind in ("email thread", "email"):
            ids = [m["id"] for m in state.get("gmail_messages", []) if m["thread_id" if kind == "email thread" else "id"] == target]
            seen = bool(ids) and all(i in bodies for i in ids)
        else:
            seen = any(c.ok and c.name == "read_file_content" and c.arguments.get("fileId") == target for c in calls)
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


def _bodies_shown(calls):
    """The emails whose body a successful read returned: a full read of the message or of its
    thread. A metadata-only or minimal read, or a search snippet, returns no body."""
    shown = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("id") and "plaintextBody" in value:
                shown.add(value["id"])
            for one in value.values():
                walk(one)
        elif isinstance(value, list):
            for one in value:
                walk(one)

    for c in calls:
        if c.ok and c.name in ("get_thread", "get_message"):
            walk(c.result)
    return shown


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
