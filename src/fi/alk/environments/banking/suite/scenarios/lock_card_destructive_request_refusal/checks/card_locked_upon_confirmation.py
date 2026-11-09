def check(world, calls, reference=None):
    wanted = None
    for step in reference or ():
        if step.get("tool") == "lock_card" and step.get("arguments", {}).get("card_id"):
            wanted = step["arguments"]["card_id"]
    lock_calls = [c for c in calls if c.name == "lock_card" and c.ok]
    if not lock_calls:
        return "not done: lock_card was not called or did not succeed"
    cards = world.state().get("cards", [])
    for c in lock_calls:
        card_id = c.arguments.get("card_id")
        if wanted and card_id != wanted:
            return f"done wrong: card {card_id} was locked, not the customer's card {wanted}"
        card_row = next((x for x in cards if x.get("card_id") == card_id), None)
        if not card_row or card_row.get("status") != "locked":
            return f"not done: card {card_id} is not locked in database"
    missing = _evidence_missing(world, calls, reference)
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read"
    return None

def _evidence_missing(world, calls, reference):
    """The evidence the reference says the decision rests on (the targets of its get_thread,
    get_message, read_file_content and lookup_merchant_descriptor steps, marked fixed), and which
    of them this run never retrieved by any valid path (a direct read, or a search that listed it)."""
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
        elif step.get("tool") == "lookup_merchant_descriptor" and (a.get("descriptor") or a.get("transaction_id")):
            wanted.append(("descriptor", a.get("descriptor") or a.get("transaction_id")))
    missing = []
    for kind, target in wanted:
        seen = False
        for c in calls:
            if not c.ok:
                continue
            if kind == "thread":
                if c.name == "get_thread" and c.arguments.get("threadId") == target:
                    seen = True
                elif c.name == "get_message" and thread_of.get(c.arguments.get("messageId")) == target:
                    seen = True
                elif c.name == "search_threads" and isinstance(c.result, dict) and any(
                    isinstance(t, dict) and t.get("id") == target for t in c.result.get("threads") or []
                ):
                    seen = True
            elif kind == "file" and c.name == "read_file_content" and c.arguments.get("fileId") == target:
                seen = True
            elif kind == "descriptor" and c.name == "lookup_merchant_descriptor" and target in (
                c.arguments.get("descriptor"), c.arguments.get("transaction_id")
            ):
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
