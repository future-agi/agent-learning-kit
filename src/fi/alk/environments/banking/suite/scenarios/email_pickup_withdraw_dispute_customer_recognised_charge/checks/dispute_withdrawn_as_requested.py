def check(world, calls, reference=None):
    wanted = None
    for step in reference or ():
        if step.get("tool") == "withdraw_dispute" and step.get("arguments", {}).get("dispute_id"):
            wanted = step["arguments"]["dispute_id"]
    made = [c for c in calls if c.name == "withdraw_dispute" and c.ok]
    if not made:
        return "not done: withdraw_dispute was not called or did not succeed"
    disputes = {d["dispute_id"]: d for d in world.state().get("disputes", [])}
    for c in made:
        did = c.arguments.get("dispute_id")
        if wanted and did != wanted:
            return f"done wrong: dispute {did} was withdrawn, the customer asked about {wanted}"
        row = disputes.get(did)
        if not row or row.get("status") != "withdrawn" or not row.get("withdrawn_at"):
            return f"not done: dispute {did} is not withdrawn in database"
    if wanted and wanted in disputes:
        owner = disputes[wanted].get("customer_id")
        others = [d for d, row in disputes.items() if row.get("customer_id") == owner and row.get("status") == "withdrawn" and d != wanted]
        if others:
            return f"done wrong: {', '.join(others)} were withdrawn as well as {wanted}"
    missing = _evidence_missing(world, calls, reference, "withdraw_dispute")
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was never read before withdraw_dispute"
    return None

def _evidence_missing(world, calls, reference, write=None):
    """The evidence the reference says the decision rests on (the targets of its get_thread,
    get_message, read_file_content and lookup_merchant_descriptor steps, marked fixed), and which
    of them this run never retrieved by any valid path. Only content counts: a thread is read when
    a get_thread returned its message bodies or a get_message returned the body of its latest
    message (the one that governs); a search listing carries snippets only, so it locates a thread
    but does not read it. A file is read when read_file_content returned it (not an old revision).
    With `write`, only what was read before the first successful call of that tool counts: evidence
    read after the decision was acted on did not inform it."""
    if write:
        acted = next((i for i, c in enumerate(calls) if c.name == write and c.ok), len(calls))
        calls = calls[:acted]
    state = world.state()
    thread_of = {m["id"]: m["thread_id"] for m in state.get("gmail_messages", [])}
    latest = {}
    for m in sorted(state.get("gmail_messages", []), key=lambda m: m["date"]):
        latest[m["thread_id"]] = m["id"]
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
            result = c.result if isinstance(c.result, dict) else {}
            if kind == "thread":
                if c.name == "get_thread" and c.arguments.get("threadId") == target and any(
                    isinstance(m, dict) and m.get("plaintextBody") for m in result.get("messages") or []
                ):
                    seen = True
                elif c.name == "get_message" and result.get("id") == latest.get(target) and result.get("plaintextBody"):
                    seen = True
            elif kind == "file" and c.name == "read_file_content" and c.arguments.get("fileId") == target and not c.arguments.get("revisionId"):
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
    # Only the verdict: a failure names the records it read, so it is not written to the console.
    print("held" if _held else "FAILED")
    raise SystemExit(0 if _held else 1)
