def check(world, calls, reference=None):
    wanted_id = wanted_tx = None
    for step in reference or ():
        a = step.get("arguments", {})
        if step.get("tool") == "get_dispute_status":
            wanted_id = a.get("dispute_id") or wanted_id
            wanted_tx = a.get("transaction_id") or wanted_tx
    status_calls = [c for c in calls if c.name == "get_dispute_status" and c.ok]
    list_calls = [c for c in calls if c.name == "list_disputes" and c.ok]
    if not status_calls and not list_calls:
        return "not done: neither get_dispute_status nor list_disputes was called"
    sessions = world.state().get("sessions", [])
    verified = {vc.arguments.get("customer_id") for vc in calls if vc.name == "verify_identity" and vc.ok}
    for c in status_calls + list_calls:
        sess = next((s for s in sessions if s.get("session_id") == c.arguments.get("session_id")), None)
        if not sess or not sess.get("is_verified"):
            return f"not done: session {c.arguments.get('session_id')} is not verified in database"
        if verified and c.arguments.get("customer_id") not in verified:
            return f"done wrong: disputes of {c.arguments.get('customer_id')} were looked up, not the verified customer's"
    if wanted_id or wanted_tx:
        found = any(
            (wanted_id and c.arguments.get("dispute_id") == wanted_id) or (wanted_tx and c.arguments.get("transaction_id") == wanted_tx)
            for c in status_calls
        ) or any(
            isinstance(c.result, dict) and any(
                isinstance(d, dict) and (d.get("dispute_id") == wanted_id or d.get("transaction_id") == wanted_tx)
                for d in c.result.get("disputes") or []
            )
            for c in list_calls
        )
        if not found:
            return f"done wrong: the dispute the customer asked about ({wanted_id or wanted_tx}) was never looked up"
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
