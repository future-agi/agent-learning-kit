def check(world, calls, reference=None):
    wanted = []
    for step in reference or ():
        if step.get("tool") == "create_return" and step.get("arguments", {}).get("order_id"):
            wanted.append((step["arguments"]["order_id"], step["arguments"].get("items") or []))
    made = [c for c in calls if c.name == "create_return" and c.ok]
    if not made:
        return "not done: create_return was not successfully called"
    state = world.state()
    returns = [r for r in state.get("returns", []) if r.get("status") != "cancelled"]
    items = state.get("return_items", [])
    # Only a return on an order the run asked about counts: the seed holds earlier returns.
    asked = {c.arguments.get("order_id") for c in made}
    if not any(r["order_id"] in asked and r.get("status") == "authorized" for r in returns):
        return "not done: no authorized return for the order in database"
    for order_id, want_items in wanted:
        if not any(c.arguments.get("order_id") == order_id for c in made):
            return f"done wrong: no return was created for order {order_id}"
        mine = [r["return_id"] for r in returns if r["order_id"] == order_id]
        for c in made:
            if c.arguments.get("order_id") != order_id and wanted:
                return f"done wrong: a return was created for order {c.arguments.get('order_id')}, not {order_id}"
        held = {}
        for it in items:
            if it["return_id"] in mine:
                held[it["sku"]] = held.get(it["sku"], 0) + int(it["quantity"])
        want = {}
        for it in want_items:
            want[it.get("sku")] = want.get(it.get("sku"), 0) + int(it.get("quantity") or 0)
        if want and held != want:
            return f"done wrong: order {order_id} has returns for {held}, the customer settled on {want}"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The evidence the reference fixes (a get_thread, get_message or read_file_content target) that
    this run never retrieved by any valid path: a direct read, or a search that listed it."""
    state = world.state()
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    wanted = []
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_thread" and a.get("threadId"):
            wanted.append(("thread", a["threadId"]))
        elif step.get("tool") == "get_message" and a.get("messageId"):
            wanted.append(("thread", thread_of.get(a["messageId"], a["messageId"])))
        elif step.get("tool") == "read_file_content" and a.get("fileId"):
            wanted.append(("file", a["fileId"]))
    missing = []
    for kind, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            if kind == "thread" and (
                (c.name == "get_thread" and c.arguments.get("threadId") == target)
                or (c.name == "get_message" and thread_of.get(c.arguments.get("messageId")) == target)
                or (c.name == "search_threads" and isinstance(c.result, dict) and any(isinstance(t, dict) and t.get("id") == target for t in c.result.get("threads") or []))
            ):
                seen = True
            elif kind == "file" and c.name == "read_file_content" and c.arguments.get("fileId") == target:
                seen = True
            if seen:
                break
        if not seen:
            missing.append(f"{kind} {target}")
    return missing


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
