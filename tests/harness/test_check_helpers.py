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
