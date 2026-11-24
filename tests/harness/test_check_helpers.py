"""The action-log helpers every check is given: a retry after a timeout, a second commit, and no commit at all."""

from __future__ import annotations

from typing import Any

from fi.alk.harness.checks import run_check


class _World:
    """A world whose action log is the rows given, read as `world.events` reads it."""

    def __init__(self, *rows: dict[str, Any]) -> None:
        self.rows = list(rows)

    def events(
        self, actor_id: str | None = None, outcome: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in self.rows
            if (actor_id is None or row.get("actor_id", "agent") == actor_id)
            and (outcome is None or row.get("outcome") == outcome)
        ]


def _row(outcome: str, record: str = "r-1", **fields: Any) -> dict[str, Any]:
    return {
        "action": "records.update",
        "arguments": {"id": record},
        "outcome": outcome,
        "effect": "write",
        "actor_id": "agent",
        **fields,
    }


def _held(check: str, world: _World) -> bool:
    return run_check(check, world, []).held


NO_RETRY = "def check(world, calls):\n    return 'retried' if retried_timeouts(world) else None\n"
ONCE = "def check(world, calls):\n    return 'committed twice' if repeated_commits(world) else None\n"
CHANGED = "def check(world, calls):\n    return None if committed_writes(world) else 'nothing was committed'\n"


def test_a_write_tried_again_on_the_record_the_world_timed_out_on_is_a_retry() -> None:
    timed_out = _row("faulted", error="timeout_before_commit")
    assert not _held(NO_RETRY, _World(timed_out, _row("committed")))
    # Answer lost after the commit: the same rule.
    assert not _held(
        NO_RETRY, _World(_row("committed", error="response_lost"), _row("committed"))
    )
    # Another record, a read, or a write that never timed out is no retry.
    assert _held(NO_RETRY, _World(timed_out, _row("committed", record="r-2")))
    assert _held(NO_RETRY, _World(timed_out, _row("committed", effect="read")))
    assert _held(NO_RETRY, _World(_row("refused"), _row("committed")))


def test_a_second_commit_on_the_same_record_is_found_and_another_actor_s_is_not() -> (
    None
):
    assert not _held(ONCE, _World(_row("committed"), _row("committed")))
    assert _held(ONCE, _World(_row("committed"), _row("committed", record="r-2")))
    assert _held(ONCE, _World(_row("committed"), _row("refused")))
    assert _held(
        ONCE, _World(_row("committed"), _row("committed", actor_id="colleague"))
    )
    # A pre-run session's row was written before the run: never the agent's second commit.
    assert _held(
        ONCE,
        _World(_row("committed", idempotency_key="t1:colleague:h1"), _row("committed")),
    )


def test_only_a_committed_write_counts_as_a_change_made() -> None:
    assert not _held(
        CHANGED, _World(_row("faulted", error="timeout_before_commit"), _row("refused"))
    )
    assert not _held(CHANGED, _World(_row("committed", effect="read")))
    assert _held(
        CHANGED,
        _World(_row("faulted", error="timeout_before_commit"), _row("committed")),
    )


REWORDED = (
    "def check(world, calls, reference):\n"
    "    return 'retried' if retried_timeouts(world, reference, own_words=['reason']) else None\n"
)
TWICE = (
    "def check(world, calls, reference):\n"
    "    return 'committed twice' if repeated_commits(world, reference, own_words=['reason']) else None\n"
)


def _refund(outcome: str, order: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {
        "action": "refunds.issue",
        "arguments": {"order_id": order, "amount": 35, "reason": reason},
        "outcome": outcome,
        "effect": "write",
        "actor_id": "agent",
        **fields,
    }


def test_a_reworded_retry_is_the_same_record_for_every_duplicate_reader() -> None:
    from fi.alk.harness.call_runner import duplicate_commits

    solution = [
        {
            "tool": "refunds.issue",
            "arguments": {"order_id": "1045", "amount": 35, "reason": "damaged"},
            "fixed": ["order_id"],
        }
    ]
    reference = [{"tool": "refunds.issue", "arguments": {"order_id": "1045"}}]
    first = _refund("committed", "1045", "damaged", error="response_lost")
    again = _refund("committed", "1045", "arrived broken")
    world = _World(first, again)
    # Rewording the free text is the same write, to the scenario's duplicate rule and to both check helpers.
    assert duplicate_commits(world.rows, solution)
    assert not run_check(REWORDED, world, [], reference=reference).held
    assert not run_check(TWICE, world, [], reference=reference).held
    # Another order is another record, to all three.
    other = _World(first, _refund("committed", "1046", "damaged"))
    assert not duplicate_commits(other.rows, solution)
    assert run_check(REWORDED, other, [], reference=reference).held
    assert run_check(TWICE, other, [], reference=reference).held


def test_a_reworded_argument_nothing_declares_as_the_agent_s_words_is_another_write() -> None:
    """Nothing is read from an argument's name: `reason` is the agent's own words only when the reference lists it
    unfixed or the check names it."""
    from fi.alk.harness.call_runner import duplicate_commits

    solution = [{"tool": "refunds.issue", "arguments": {"order_id": "1045"}, "fixed": ["order_id"]}]
    reference = [{"tool": "refunds.issue", "arguments": {"order_id": "1045"}}]
    world = _World(_refund("committed", "1045", "damaged", error="response_lost"), _refund("committed", "1045", "broken"))
    unnamed = "def check(world, calls, reference):\n    return 'twice' if repeated_commits(world, reference) else None\n"

    assert duplicate_commits(world.rows, solution) == []
    assert run_check(unnamed, world, [], reference=reference).held


def test_a_log_kept_before_outcomes_were_recorded_still_lists_its_committed_writes() -> (
    None
):
    old = {
        "action": "records.update",
        "arguments": '{"id": "r-1"}',
        "ok": True,
        "effect": "write",
        "actor_id": "agent",
    }
    assert _held(CHANGED, _World(old))
    assert not _held(CHANGED, _World({**old, "ok": False}))


def test_the_proof_world_s_log_carries_what_the_helpers_read() -> None:
    from types import SimpleNamespace

    from fi.alk.harness.prove import _WorldActors

    failed = SimpleNamespace(
        name="update", arguments={"id": "r-1"}, result=None, ok=False, error="timeout"
    )
    done = SimpleNamespace(
        name="update", arguments={"id": "r-1"}, result={}, ok=True, error=""
    )
    proof = _WorldActors.__new__(_WorldActors)
    proof.world = SimpleNamespace(calls=[failed, done])
    proof.refusals, proof.by_actors = [], {}
    rows = proof.events(actor_id="agent")
    assert [row["error"] for row in rows] == ["timeout", None]
    assert len({row["action_id"] for row in rows}) == 2
