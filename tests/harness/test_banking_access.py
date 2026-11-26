"""Who the bank's tools serve, and what a repeated or wrong request gets back: the caller is set by
the session and no ID, session or passcode the desk is told changes it, the mailbox opens only the
caller's mail (a support colleague works all of it), a request key replays only its own request, and
verification is passcode or KBA first with one one-time passcode after it."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import run_steps
from fi.alk.harness.releases import WORLD
from fi.alk.harness.world.snapshot import restore

HERE = Path(environments.__file__).parent / "banking"
SUITE = HERE / "suite"
KEYS = sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("banking")
    designed.build_environment(HERE, root / "environment")
    shutil.copytree(root / "environment" / WORLD, root / WORLD)
    return root


@pytest.fixture
def world(built: Path):
    world = restore(built / WORLD)
    yield world
    world.close()


def _sign_in(world, customer_id=None, staff_id=None) -> None:
    world.change("session_caller", "default", {"customer_id": customer_id, "staff_id": staff_id}, by="id")


def _ok(world, tool: str, **arguments):
    call = world.call(tool, arguments)
    assert call.ok, (tool, call.error)
    return call.result


def _refused(world, tool: str, word: str, **arguments) -> str:
    before = world.state()
    call = world.call(tool, arguments)
    assert not call.ok and word in call.error, (tool, call.error, call.result)
    assert world.state() == before, tool
    return call.error


# --- the caller: every tool that reads or changes a customer's records ----------------------------

# Alice (C01) in her own, already verified session: each call is right for her and for nobody else.
ALICE = {"customer_id": "C01", "session_id": "SESS01"}
RECORD_CALLS = [
    ("verify_identity", {"challenge_type": "passcode", "challenge_response": "8194"}),
    ("get_customer_profile", {}),
    ("list_accounts", {}),
    ("list_owned_cards", {}),
    ("list_transactions", {}),
    ("get_transaction", {"transaction_id": "TX17"}),
    ("lookup_merchant_descriptor", {"transaction_id": "TX17"}),
    ("list_disputes", {}),
    ("lock_card", {"card_id": "CARD01", "reason": "lost_card", "confirmed": True}),
    ("request_replacement_card", {"card_id": "CARD02", "reason": "damaged", "confirmed": True}),
    ("update_card_controls", {"card_id": "CARD02", "online_enabled": False, "confirmed": True}),
    ("create_dispute", {"transaction_id": "TX17", "dispute_reason": "unrecognized_charge"}),
    ("get_dispute_status", {"transaction_id": "TX17"}),
    ("withdraw_dispute", {"dispute_id": "D06", "reason": "recognised", "confirmed": True}),
    ("update_contact_details", {"field": "phone", "new_value": "+15551239999"}),
    ("create_handoff", {"reason": "customer_requested_agent"}),
]


def test_every_tool_that_takes_a_customer_is_covered() -> None:
    tools = json.loads((HERE / "tools.json").read_text(encoding="utf-8"))
    taking = {t["name"] for t in tools if '"customer_id"' in json.dumps(t)}
    assert {tool for tool, _ in RECORD_CALLS} == taking


@pytest.mark.parametrize(("tool", "arguments"), RECORD_CALLS, ids=[t for t, _ in RECORD_CALLS])
def test_another_customer_is_refused_with_the_right_ids_and_session(world, tool: str, arguments: dict) -> None:
    _sign_in(world, "C02")
    _refused(world, tool, "permission_denied", **ALICE, **arguments)


@pytest.mark.parametrize(("tool", "arguments"), RECORD_CALLS, ids=[t for t, _ in RECORD_CALLS])
def test_nobody_signed_in_is_served_only_a_handoff(world, tool: str, arguments: dict) -> None:
    if tool == "create_handoff":
        _ok(world, tool, **ALICE, **arguments)
    else:
        _refused(world, tool, "permission_denied", **ALICE, **arguments)


def test_the_signed_in_customer_is_served_every_call(world) -> None:
    _sign_in(world, "C01")
    for tool, arguments in RECORD_CALLS:
        if tool == "update_contact_details":
            _ok(world, "verify_identity", **ALICE, challenge_type="otp", challenge_response="482903")
        _ok(world, tool, **ALICE, **arguments)


# --- the mailbox ----------------------------------------------------------------------------------

THREAD, MESSAGE, EMAIL = "th_c01_complaint", "msg_003", "alice.smith@example.com"
MAIL_CALLS = [
    ("get_thread", {"threadId": THREAD}),
    ("get_message", {"messageId": MESSAGE}),
    ("label_thread", {"threadId": THREAD, "labelIds": ["STARRED"]}),
    ("unlabel_thread", {"threadId": THREAD, "labelIds": ["INBOX"]}),
    ("create_draft", {"replyToMessageId": MESSAGE, "to": ["bob.smith@example.com"], "body": "hi"}),
    ("create_draft", {"to": [EMAIL], "subject": "x", "body": "hi"}),
    ("create_draft", {"to": ["bob.smith@example.com"], "cc": [EMAIL], "subject": "x", "body": "hi"}),
]


@pytest.mark.parametrize("caller", ["C02", None])
@pytest.mark.parametrize(("tool", "arguments"), MAIL_CALLS)
def test_another_customers_mail_is_refused(world, caller, tool: str, arguments: dict) -> None:
    _sign_in(world, caller)
    _refused(world, tool, "permission_denied", **arguments)


def test_search_and_drafts_show_a_customer_only_their_own_mail(world) -> None:
    _sign_in(world, staff_id="ST01")
    _ok(world, "create_draft", replyToMessageId=MESSAGE, to=[EMAIL], body="Dear Alice")
    for query in ("", "Target", "from:alice.smith@example.com", "subject:Unauthorized", "label:INBOX"):
        for caller, own in (("C02", "th_c02_"), ("C01", "th_c01_")):
            _sign_in(world, caller)
            found = [t["id"] for t in _ok(world, "search_threads", query=query, pageSize=50)["threads"]]
            assert all(t.startswith(own) for t in found), (query, caller, found)
    assert [d["threadId"] for d in _ok(world, "list_drafts")["drafts"]] == [THREAD]
    for caller in ("C02", None):
        _sign_in(world, caller)
        assert _ok(world, "list_drafts")["drafts"] == []
    assert _ok(world, "search_threads", query="")["threads"] == []


def _put_thread(world, thread: str, sender: str, recipient: str, cc: str = "") -> None:
    world.put("gmail_threads", {"id": thread, "subject": "s", "snippet": "s", "history_id": "h", "is_restricted": 0, "updated_at": "2026-10-25T10:00:00Z"})
    world.put("gmail_messages", {"id": f"m_{thread}", "thread_id": thread, "sender": sender, "recipient": recipient, "cc": cc, "subject": "s", "body": "b", "date": "2026-10-25T10:00:00Z", "snippet": "s"})


DESK = "support@apexhorizonbank.com"


@pytest.mark.parametrize(
    ("sender", "recipient", "cc", "opens"),
    [
        ("Alice Smith <ALICE.SMITH@example.com>", DESK, "", True),
        (f"Desk <{DESK}>", "alice.smith@example.com", "", True),
        ("xalice.smith@example.com", DESK, "", False),
        ("alice.smith@example.com.evil.test", DESK, "", False),
        ('"alice.smith@example.com" <mallory@evil.test>', DESK, "", False),
        ("alice.smith@example.com", DESK, "bob.smith@example.com", False),
        (DESK, DESK, "", False),
    ],
)
def test_mail_is_matched_by_the_complete_address_of_everyone_on_it(world, sender, recipient, cc, opens) -> None:
    _put_thread(world, "th_probe", sender, recipient, cc)
    _sign_in(world, "C01")
    if opens:
        assert _ok(world, "get_thread", threadId="th_probe")["messages"]
    else:
        _refused(world, "get_thread", "permission_denied", threadId="th_probe")
    assert ("th_probe" in [t["id"] for t in _ok(world, "search_threads", query="", pageSize=50)["threads"]]) is opens
    _sign_in(world, staff_id="ST01")
    assert _ok(world, "get_thread", threadId="th_probe")["messages"]


def test_a_colleague_works_the_mailbox_but_no_customers_records(world) -> None:
    _sign_in(world, staff_id="ST01")
    _ok(world, "get_thread", threadId=THREAD)
    _ok(world, "create_draft", replyToMessageId=MESSAGE, to=[EMAIL], body="Dear Alice")
    _refused(world, "get_thread", "permission_denied", threadId="th_c06_restricted_fraud")
    _refused(world, "list_owned_cards", "permission_denied", **ALICE)
    world.change("support_staff", "ST01", {"active": 0}, by="staff_id")
    _refused(world, "get_thread", "permission_denied", threadId=THREAD)


@pytest.mark.parametrize("key", KEYS)
def test_every_setup_signs_in_the_person_in_the_conversation(built: Path, key: str) -> None:
    assert "session_caller" in (SUITE / "scenarios" / key / "setup.py").read_text(encoding="utf-8"), key
    scenario = read_folder(SUITE, key)
    world, *_ = run_steps(scenario, built / WORLD, with_solution=False)
    try:
        state = world.state()
    finally:
        world.close()
    [caller] = state["session_caller"]
    name = scenario.persona.name
    customer = next((c["customer_id"] for c in state["customers"] if f"{c['first_name']} {c['last_name']}" == name), None)
    staff = next((s["staff_id"] for s in state["support_staff"] if s["name"] == name), None)
    assert (caller["customer_id"], caller["staff_id"]) == (customer, staff), (key, name)


# --- request keys ---------------------------------------------------------------------------------

BOB = {"customer_id": "C02", "session_id": "SESS02"}


def _verified_bob(world) -> None:
    _sign_in(world, "C02")
    _ok(world, "verify_identity", **BOB, challenge_type="passcode", challenge_response="5371")
    _ok(world, "verify_identity", **BOB, challenge_type="otp", challenge_response="918274")


def _verified_alice(world) -> None:
    _sign_in(world, "C01")
    _ok(world, "verify_identity", **ALICE, challenge_type="otp", challenge_response="482903")


KEYED = [
    (
        "create_dispute",
        {"transaction_id": "TX17", "dispute_reason": "unrecognized_charge"},
        [{"transaction_id": "TX19"}, {"dispute_reason": "incorrect_amount"}],
        {"transaction_id": "TX04", "dispute_reason": "unrecognized_charge"},
    ),
    (
        "update_contact_details",
        {"field": "phone", "new_value": "+15551230001"},
        [{"new_value": "+15551230002"}, {"field": "address", "new_value": "1 Elm Street"}],
        {"field": "phone", "new_value": "+15551230001"},
    ),
    (
        "request_replacement_card",
        {"card_id": "CARD01", "reason": "damaged", "confirmed": True},
        [{"card_id": "CARD02"}, {"reason": "expired"}, {"delivery_speed": "expedited"}],
        {"card_id": "CARD03", "reason": "damaged", "confirmed": True},
    ),
]


SAME = ("dispute_id", "change_id", "replacement_id", "transaction_id", "card_id", "new_value", "request_key")


def test_every_tool_with_a_request_key_is_covered() -> None:
    tools = json.loads((HERE / "tools.json").read_text(encoding="utf-8"))
    assert {tool for tool, *_ in KEYED} == {t["name"] for t in tools if '"request_key"' in json.dumps(t)}


@pytest.mark.parametrize(("tool", "asked", "changes", "bobs"), KEYED, ids=[k[0] for k in KEYED])
def test_a_request_key_replays_only_its_own_request(world, tool, asked, changes, bobs) -> None:
    _verified_alice(world)
    first = _ok(world, tool, **ALICE, **asked, request_key="K1")
    assert not first["idempotent"]
    replay = _ok(world, tool, **ALICE, **asked, request_key="K1")
    assert replay["idempotent"] and {k: v for k, v in replay.items() if k in SAME} == {k: v for k, v in first.items() if k in SAME}
    for change in changes:
        _refused(world, tool, "conflict", **ALICE, **{**asked, **change}, request_key="K1")
    _verified_bob(world)
    said = _refused(world, tool, "conflict", **BOB, **bobs, request_key="K1")
    for private in ("TX17", "CARD01", "CARD02", "C01", "8420", "alice", first.get("dispute_id") or first.get("change_id") or first.get("replacement_id")):
        assert private not in said


# --- verification ---------------------------------------------------------------------------------


def _verify(world, kind: str, answer: str):
    return world.call("verify_identity", {**BOB, "challenge_type": kind, "challenge_response": answer})


def _session(world) -> dict:
    return next(s for s in world.state()["sessions"] if s["session_id"] == "SESS02")


def test_verifying_more_than_once_never_collides(world) -> None:
    _sign_in(world, "C02")
    for kind, answer, ok in (
        ("passcode", "0000", False),
        ("passcode", "5371", True),
        ("kba", "Fluffy", True),
        ("passcode", "5371", True),
        ("passcode", "0000", False),
    ):
        call = _verify(world, kind, answer)
        assert call.ok is ok and (ok or "Verification failed" in call.error), (kind, call.error)
    # The second wrong passcode is counted, not rolled back with a colliding log row.
    assert _session(world)["failed_attempts"] == 1
    log = world.state()["action_log"]
    assert len(log) == 5 == len({row["log_id"] for row in log})


def test_neither_a_step_up_nor_a_repeat_verification_clears_the_failed_attempts(world) -> None:
    _sign_in(world, "C02")
    assert _verify(world, "passcode", "5371").ok
    for _ in range(2):
        assert not _verify(world, "otp", "000000").ok
    assert _verify(world, "passcode", "5371").ok
    assert _verify(world, "kba", "Fluffy").ok
    assert _verify(world, "otp", "918274").ok
    assert _session(world)["failed_attempts"] == 2
    assert not _verify(world, "passcode", "0000").ok
    locked = _verify(world, "passcode", "5371")
    assert not locked.ok and "Verification locked" in locked.error


def test_the_step_up_needs_a_passcode_or_kba_first_and_spends_its_otp(world) -> None:
    _sign_in(world, "C02")
    first = _verify(world, "otp", "918274")
    assert not first.ok and "passcode or KBA first" in first.error
    assert not _session(world)["is_verified"] and not _session(world)["step_up_at"]
    assert _verify(world, "kba", "Fluffy").ok and not _session(world)["step_up_at"]
    assert _verify(world, "otp", "918274").result["step_up"] is True
    again = _verify(world, "otp", "918274")
    assert not again.ok and "already been used" in again.error
    assert _session(world)["failed_attempts"] == 0
    _ok(world, "update_contact_details", **BOB, field="phone", new_value="+15551230001")


# --- the policy's parameters ----------------------------------------------------------------------


def test_every_policy_parameter_is_read_by_a_handler(world) -> None:
    rules = json.loads((HERE / "policy.json").read_text(encoding="utf-8"))["rules"]
    parameters = {name for rule in rules for name in rule.get("parameters") or {}}
    handlers = "\n".join(p.read_text(encoding="utf-8") for p in (HERE / "handlers").glob("*.py"))
    assert not {name for name in parameters if f"'{name}'" not in handlers and f'"{name}"' not in handlers}
    assert {row["name"] for row in world.state()["environment_policy"]} == parameters
