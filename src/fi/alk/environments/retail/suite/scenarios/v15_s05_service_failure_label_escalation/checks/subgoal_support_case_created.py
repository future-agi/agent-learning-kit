def check(world, calls, reference=None):
    wanted_customer = wanted_order = None
    for step in reference or ():
        if step.get("tool") == "create_support_case":
            wanted_customer = step.get("arguments", {}).get("customer_id") or wanted_customer
            wanted_order = step.get("arguments", {}).get("order_id") or wanted_order
    case_calls = [c for c in calls if c.name == "create_support_case" and c.arguments.get("customer_id") and c.arguments.get("order_id") and c.ok]
    if not case_calls:
        return "not done: create_support_case was not called with customer_id and order_id"
    cases = world.state().get("support_cases", [])
    if not cases:
        return "not done: support case record not found in database"
    for c in case_calls:
        if wanted_customer and c.arguments.get("customer_id") != wanted_customer:
            return f"done wrong: the case was opened for {c.arguments.get('customer_id')}, not {wanted_customer}"
        if wanted_order and c.arguments.get("order_id") != wanted_order:
            return f"done wrong: the case was opened on order {c.arguments.get('order_id')}, not {wanted_order}"
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
