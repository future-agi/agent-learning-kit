def check(world, calls, reference=None):
    import re
    state = world.state()
    messages = state.get("gmail_messages", [])
    thread_of = {m["id"]: m["thread_id"] for m in messages}
    wanted_thread = None
    for step in reference or ():
        if step.get("tool") == "create_draft":
            wanted_thread = thread_of.get(step.get("arguments", {}).get("replyToMessageId")) or step.get("arguments", {}).get("thread_id") or wanted_thread
    draft_calls = [c for c in calls if c.name == "create_draft" and c.ok]
    if not draft_calls:
        return "not done: create_draft was not called"
    drafts = state.get("gmail_drafts", [])
    if not drafts:
        return "not done: no draft found in database"
    if wanted_thread and not any(d.get("thread_id") == wanted_thread for d in drafts):
        return f"done wrong: no draft on the customer's thread {wanted_thread!r}"
    threads = [wanted_thread] if wanted_thread else sorted({d.get("thread_id") for d in drafts})
    bank = "@apexhorizonbank.com"
    customers = {c["email"].lower(): c for c in state.get("customers", [])}
    disputes = state.get("disputes", [])
    files = state.get("drive_files", [])
    effective_fees = " ".join((f.get("content") or "") for f in files if f.get("policy_family") == "fee_schedule" and f.get("status") == "approved")
    stale_fees = " ".join((f.get("content") or "") for f in files if f.get("policy_family") == "fee_schedule" and f.get("status") in ("superseded", "draft"))
    for thread in threads:
        if not any(c.name in ("get_thread", "get_message", "search_threads") and c.ok for c in calls):
            return f"done blind: a reply was drafted on {thread!r} without reading the mailbox"
        in_thread = sorted((m for m in messages if m["thread_id"] == thread), key=lambda m: m["date"])
        customer_email = next((m["sender"].lower() for m in in_thread if not m["sender"].lower().endswith(bank)), None)
        customer = customers.get(customer_email or "")
        theirs = {d["dispute_id"]: d for d in disputes if customer and d.get("customer_id") == customer["customer_id"]}
        for d in (x for x in drafts if x.get("thread_id") == thread):
            recipients = [r.strip().lower() for r in (d.get("recipient") or "").split(",")]
            if customer_email and customer_email not in recipients:
                return f"done wrong: the draft on {thread!r} is addressed to {recipients}, not to the customer {customer_email}"
            body = d.get("body") or ""
            lower = body.lower()
            named = set(re.findall(r"\bD\d{2,}\b", body))
            for did in sorted(named):
                if did not in theirs:
                    return f"done wrong: the draft names dispute {did}, which is not this customer's"
            if len(named) == 1:
                status = next(iter(theirs[d2]["status"] for d2 in named))
                words = {"under review": "under_review", "under_review": "under_review", "resolved": "resolved", "withdrawn": "withdrawn", "still open": "open"}
                said = {s for w, s in words.items() if w in lower}
                if said and status not in said:
                    return f"done wrong: the draft describes the dispute as {', '.join(sorted(said))} but the record says {status}"
            for amount in re.findall(r"\$\d[\d,]*\.\d{2}", body):
                if amount in stale_fees and amount not in effective_fees:
                    return f"done wrong: the draft quotes {amount}, a fee from a superseded or draft schedule"
            if "provisional credit" in lower and re.search(r"\b(approved|granted|issued|credited|will receive|has been applied|we have applied)\b", lower):
                return "done wrong: the draft promises provisional credit, which the desk never grants"
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
