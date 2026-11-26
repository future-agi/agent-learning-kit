def check(world, calls, reference=None):
    # The caller says they emailed; the mailbox holds nothing from them about the charge. The agent
    # must have looked (a successful search_threads aimed at the caller or the charge) and the
    # search must really have come back without a matching email, before anything was filed.
    cid = next((s.get("arguments", {}).get("customer_id") for s in reference or () if s.get("tool") == "verify_identity" and s.get("arguments", {}).get("customer_id")), None)
    tx_id = next((s.get("arguments", {}).get("transaction_id") for s in reference or () if s.get("tool") == "create_dispute" and s.get("arguments", {}).get("transaction_id")), None)
    state = world.state()
    customer = next((c for c in state.get("customers", []) if c["customer_id"] == cid), None)
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == tx_id), None)
    if customer is None or tx is None:
        return "not done: the reference names no caller and charge to look for"
    email = customer["email"].lower()
    aims = {email, customer["last_name"].lower(), customer["first_name"].lower(), tx["merchant_name"].lower(), tx_id.lower()}
    aims |= set(tx["merchant_name"].lower().split())
    searches = [c for c in calls if c.name == "search_threads" and c.ok and isinstance(c.result, dict)
                and any(a in (c.arguments.get("query") or "").lower() for a in aims)]
    if not searches:
        return f"not done: the mailbox was never searched for {customer['first_name']} {customer['last_name']}'s email"
    # The search settles what to do next: carry on from the chat. It must come before the filing,
    # and the filing must have taken effect (the charge has its dispute record).
    filings = [i for i, c in enumerate(calls) if c.ok and c.name == "create_dispute" and c.arguments.get("transaction_id") == tx_id]
    if not filings or not any(d.get("transaction_id") == tx_id for d in state.get("disputes", [])):
        return f"not done: after the search nothing was filed for {tx_id}"
    if not any(calls.index(c) < filings[0] for c in searches):
        return "done blind: the mailbox was searched only after acting"
    # The premise itself: no message from the caller mentions the charge.
    for m in state.get("gmail_messages", []):
        text = f"{m.get('subject', '')} {m.get('body', '')}".lower()
        if m.get("sender", "").lower() == email and (tx_id.lower() in text or tx["merchant_name"].lower() in text):
            return f"premise broken: {m['id']} from {email} is about {tx_id}"
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
    # Only the verdict: a failure names the records it read, so it is not written to the console.
    print("held" if _held else "FAILED")
    raise SystemExit(0 if _held else 1)
