def check(world, calls, reference=None):
    wanted = next((s.get("arguments", {}) for s in reference or () if s.get("tool") == "slack_send_message"), {})
    st = world.state()
    delivered = [r for r in st.get("slack_message_ledger", []) if r.get("status") == "delivered"]
    if not delivered:
        return "not done: no delivered record in message_ledger"
    key = wanted.get("request_key")
    if key and not any(r.get("request_key") == key for r in delivered):
        return f"done wrong: expected request_key in ledger to be {key!r}"
    messages = {m["message_id"]: m for m in st.get("slack_messages", [])}

    def plain(text):
        return " ".join((text or "").split())

    for r in delivered:
        posted = messages.get(r.get("message_id"))
        if posted is None:
            return "not done: message not recorded in messages table"
        if wanted.get("channel_id") and posted["channel_id"] != wanted["channel_id"]:
            return f"done wrong: posted to {posted['channel_id']!r}, expected {wanted['channel_id']!r}"
        if wanted.get("message") and plain(posted["text"]) != plain(wanted["message"]):
            return "done wrong: the posted text is not the approved digest"
    if len(delivered) > 1:
        return "done wrong: the digest was posted more than once"
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
    print("held" if _held else f"FAILED: {_said}")
    raise SystemExit(0 if _held else 1)
