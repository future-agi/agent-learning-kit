def check(world, calls, reference=None):
    import json
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
    # The customer's emails themselves, every message of the thread, returned with their bodies: a
    # search hit or a snippet is not reading what they asked.
    shown = _bodies_shown(calls)
    for thread in ([wanted_thread] if wanted_thread else sorted({d["thread_id"] for d in drafts})):
        ids = [m["id"] for m in messages if m["thread_id"] == thread]
        if ids and not all(i in shown for i in ids):
            return f"done blind: a reply was drafted on {thread!r} without reading its messages"
    store = "@northgate-outfitters.example"
    customers = {c["email"].lower(): c for c in state.get("customers", [])}
    returns = {r["return_id"]: r for r in state.get("returns", [])}
    category = {p["sku"]: p["category"] for p in state.get("products", [])}
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
            if re.search(r"(refund\b(?:(?!\b(?:once|when|after|until|if|will|would|before|can)\b)[^.]){0,30}\b((has been|was) (issued|processed|sent|completed|paid)|is (now )?(complete|completed|done)|is (now )?(issued|paid|processed)(?! (within|after|once|when|in)\b))|we have refunded|(has|have) been (refunded|credited)|was (refunded|credited))", low):
                return "done wrong: the draft says a refund was issued; a return authorization is not a refund"
            # Keeping the goods with no return is an exception no record grants.
            if re.search(r"(keep (all )?(of )?(the |your )?(goods|items?|products?|merchandise)|no return (is )?(needed|required|necessary)|(do not|don't) need to (send|return))", low):
                return "done wrong: the draft waives the return, an exception no record grants"
            # Every amount, tracking number and order status the draft states is one this run was
            # shown: a price, net, discount or balance a tool returned, or a figure the customer's
            # own email quotes; a number from nowhere is a claim the records do not back.
            amounts, texts = _shown(calls)
            for amount in re.findall(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)", body):
                if round(float(amount.replace(",", "")) * 100) not in amounts:
                    return f"done wrong: the draft states ${amount}, which no record this run read holds"
            for number in re.findall(r"\b1Z[0-9A-Z]{8,}\b", body):
                if not any(number in t for t in texts):
                    return f"done wrong: the draft gives tracking number {number}, which no record this run read holds"
            orders = {o["order_id"]: o for o in state.get("orders", [])}
            for oid in set(re.findall(r"\bO\d{2,3}\b", body)):
                status = orders.get(oid, {}).get("status")
                says = " ".join(s.lower() for s in re.split(r"(?<=[.!?])\s+", body) if oid in s)
                if re.search(r"\b(has been|was|is now|is) cancell?ed\b", says) and status != "cancelled":
                    return f"done wrong: the draft says order {oid} was cancelled; it is {status}"
                if re.search(r"(remains active|is processing|still processing|will ship|not (been )?cancell?ed|in transit|has shipped|was delivered|been delivered)", says):
                    if status == "cancelled" and "not" not in says:
                        return f"done wrong: the draft says order {oid} is active; it was cancelled"
                    if not any(c.ok and c.name in ("get_order", "get_shipment_tracking", "search_orders", "cancel_order") and oid in json.dumps(c.result, default=str) for c in calls):
                        return f"done wrong: the draft states the status of order {oid}, which this run never read"
            quoted = set(re.findall(r"\b(\d+)[- ]days?\b", low))
            for days in quoted:
                if days in ("21", "60", "90"):
                    return f"done wrong: the draft quotes a {days}-day window from a superseded, deleted or draft policy"
            # 14 days is the accessories window; for a thread about footwear or apparel only, it is
            # the superseded 2025 window.
            asked = {category.get(s) for m in in_thread for s in re.findall(r"\b(?:SH|FS|AP|AC)\d{2}\b", f"{m['subject']} {m['body']}")}
            if "14" in quoted and "30" not in quoted and asked and asked <= {"Footwear", "Apparel"}:
                return "done wrong: the draft quotes 14 days for footwear or apparel, which is the superseded window"
            says_none = re.search(r"(no record|could not find|cannot find|can't find|does not exist|no such return|not on file)", low)
            claimed = set(re.findall(r"\bR\d{2,4}\b", " ".join(m["body"] for m in in_thread)))
            for rid in set(re.findall(r"\bR\d{2,4}\b", body)):
                if rid in returns and cust and returns[rid]["customer_id"] != cust["customer_id"]:
                    return f"done wrong: the draft names return {rid}, which is another customer's"
                # Only a return the customer claimed may be named as missing; any other is invented.
                if rid not in returns and not (says_none and rid in claimed):
                    return f"done wrong: the draft treats return {rid} as real, but no such return exists"
    missing = _evidence_missing(world, calls, reference, "create_draft")
    if missing:
        return f"done blind: the decision rests on {', '.join(missing)}, which was not read first"
    return None

def _evidence_missing(world, calls, reference, *writes):
    """The evidence the reference fixes and whether its content reached this run: every message of an
    email thread (or the one email) with its body returned by a read, and a library document read.
    Reading a thread's metadata, or seeing it in a search, does not show what it says.
    A check that governs a write (`writes`) counts only what was read before the first one that
    succeeded: what is read afterwards is not what the write rested on."""
    first = next((i for i, c in enumerate(calls) if c.ok and c.name in writes), len(calls))
    calls = calls[:first]
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


def _shown(calls):
    """What this run was shown: every whole-cent amount a successful call returned (a field in
    cents, or a dollar figure in a text such as an email), and every text it returned."""
    import re
    amounts, texts = set(), []

    def walk(value, key=""):
        if isinstance(value, dict):
            for k, one in value.items():
                walk(one, k)
        elif isinstance(value, list):
            for one in value:
                walk(one, key)
        elif isinstance(value, bool):
            return
        elif isinstance(value, int) and key.endswith("_cents"):
            amounts.add(abs(value))
        elif isinstance(value, str):
            texts.append(value)
            for figure in re.findall(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)", value):
                amounts.add(round(float(figure.replace(",", "")) * 100))

    for c in calls:
        if c.ok and c.name != "create_draft":
            walk(c.result)
    return amounts, texts


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
