def check(world, calls, reference=None):
    import re
    state = world.state()
    messages = state.get("gmail_messages", [])
    thread_of = {m["id"]: m["thread_id"] for m in messages}
    wanted_thread = None
    for step in reference or ():
        if step.get("tool") == "create_draft":
            wanted_thread = thread_of.get(step.get("arguments", {}).get("replyToMessageId")) or wanted_thread
    if not any(c.name == "create_draft" and c.ok for c in calls):
        return "not done: create_draft was not called"
    drafts = state.get("gmail_drafts", [])
    if not drafts:
        return "not done: no draft found in database"
    if wanted_thread and not any(d.get("thread_id") == wanted_thread for d in drafts):
        return f"done wrong: no draft on the customer's thread {wanted_thread!r}"
    if not any(c.name in ("get_thread", "get_message", "search_threads") and c.ok for c in calls):
        return "done blind: a reply was drafted without reading the mailbox"
    store = "@northgate-outfitters.example"
    customers = {c["email"].lower(): c for c in state.get("customers", [])}
    returns = {r["return_id"]: r for r in state.get("returns", [])}
    for thread in ([wanted_thread] if wanted_thread else sorted({d["thread_id"] for d in drafts})):
        in_thread = sorted((m for m in messages if m["thread_id"] == thread), key=lambda m: m["date"])
        email = next((m["sender"].lower() for m in in_thread if not m["sender"].lower().endswith(store)), None)
        cust = customers.get(email or "")
        for d in (x for x in drafts if x.get("thread_id") == thread):
            to = [r.strip().lower() for r in (d.get("recipient") or "").split(",")]
            if email and email not in to:
                return f"done wrong: the draft on {thread!r} is addressed to {to}, not to the customer {email}"
            body = d.get("body") or ""
            low = body.lower()
            if re.search(r"(refund (has been|was) (issued|processed|sent)|we have refunded|refunded to your|credited to your (card|account))", low):
                return "done wrong: the draft says a refund was issued; a return authorization is not a refund"
            for days in re.findall(r"\b(\d+)[- ]days?\b", low):
                if days in ("21", "60"):
                    return f"done wrong: the draft quotes a {days}-day window from a superseded or draft policy"
            says_none = re.search(r"(no record|could not find|cannot find|can't find|does not exist|no such return|not on file)", low)
            for rid in set(re.findall(r"\bR\d{2}\b", body)):
                if rid in returns and cust and returns[rid]["customer_id"] != cust["customer_id"]:
                    return f"done wrong: the draft names return {rid}, which is another customer's"
                if rid not in returns and not says_none:
                    return f"done wrong: the draft treats return {rid} as real, but no such return exists"
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
