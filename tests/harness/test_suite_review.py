"""A review that crashed must not be readable as a review that approved.

`submit_gaps` asks for an empty list when the suite is covering what it should, so `[]` is the
documented success signal. Returning it from the exception handler made a transient model error
indistinguishable from "reviewed, and this suite is complete" -- and the top-up loop only runs
while the suite is below target, so review is the mechanism for reaching `wanted`. One crash
ended the loop and a short suite was saved and reported as the finished product.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from fi.alk.harness import scenarios


class _Contract:
    agent = "acme"
    real_use_cases = ["book a table", "cancel a booking"]

    def brief(self, with_data: bool = False) -> str:
        return "an agent"


class _Exploding:
    """A Stage whose session cannot be opened, which is what a transient model error looks like."""

    opened = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        type(self).opened += 1
        raise RuntimeError("the model provider returned 503")

    async def __aexit__(self, *exc):
        return False


def _quiet_suite(monkeypatch, tmp_path, produced: int):
    """write_in_parallel with its I/O and its writers stubbed, so only the loop is under test."""
    written = [SimpleNamespace(name=f"s{index}") for index in range(produced)]
    # The whole suite comes back from the first slice and nothing from the rest, so the total is
    # `produced` however many slices `planned` decides on. What matters here is only that the
    # suite ends up below `wanted`, which is the condition the top-up loop runs under.
    first = {"done": False}

    async def _slice(*args, **kwargs):
        if first["done"]:
            return []
        first["done"] = True
        return list(written)

    monkeypatch.setattr(scenarios, "_write_slice", _slice)
    monkeypatch.setattr(scenarios, "merged", lambda groups: [one for g in groups for one in g])
    monkeypatch.setattr(scenarios, "load_scenarios", lambda destination: [])
    monkeypatch.setattr(scenarios, "write_scenarios", lambda *a, **k: None)
    monkeypatch.setattr(scenarios, "load_catalogue", lambda destination: None)
    monkeypatch.setattr(scenarios, "load", lambda destination: list(written))
    return written


def test_a_review_that_crashed_is_not_a_review_that_approved(monkeypatch, tmp_path):
    """The unit of the conflation: `gaps_in` must not answer a crash with the value that means
    the suite is finished."""
    _Exploding.opened = 0
    monkeypatch.setattr(scenarios, "Stage", _Exploding)

    review = asyncio.run(
        scenarios.gaps_in(
            _Contract(),
            [SimpleNamespace(name="s0", use_case="u", branch="b", tests="t")],
            destination=tmp_path,
            wanted=20,
        )
    )
    assert review.reviewed is False
    assert review.gaps == []
    # The two states the old return value collapsed: no gaps found, versus nobody looked.
    assert review.complete is False
    assert scenarios.SuiteReview(reviewed=True, gaps=[]).complete is True
    assert "503" in review.reason


def test_a_failed_review_does_not_end_the_top_up_or_pass_unreported(monkeypatch, tmp_path):
    """What the conflation cost in practice. Ask for 20, have the writers produce 12, and let the
    review session raise: the old code returned [], `if not missing: break` fired, and a
    12-scenario suite was saved as finished with nothing logged anywhere."""
    _Exploding.opened = 0
    monkeypatch.setattr(scenarios, "Stage", _Exploding)
    _quiet_suite(monkeypatch, tmp_path, produced=12)

    seen: list[dict] = []
    suite = asyncio.run(
        scenarios.write_in_parallel(
            _Contract(),
            out=tmp_path,
            wanted=20,
            rounds=3,
            on_event=seen.append,
        )
    )

    # The suite as written is still kept: a failed review must not take the run down.
    assert len(suite) == 12
    # But the failure is visible, and it did not quietly count as approval.
    failures = [event for event in seen if event.get("type") == "review_failed"]
    assert failures, "a review that never ran must not pass silently"
    assert "503" in failures[0]["why"]
    # And every remaining round was tried rather than abandoned on the first crash.
    assert _Exploding.opened == 3, f"review attempted {_Exploding.opened} times, expected 3"
    assert not [event for event in seen if event.get("type") == "topping_up"]


def test_a_genuinely_complete_suite_still_ends_the_loop(monkeypatch, tmp_path):
    """The fix must not turn "finished" into "keep trying": an approving review still stops."""
    _quiet_suite(monkeypatch, tmp_path, produced=12)
    calls = {"n": 0}

    async def _approves(*args, **kwargs):
        calls["n"] += 1
        return scenarios.SuiteReview(reviewed=True, gaps=[])

    monkeypatch.setattr(scenarios, "gaps_in", _approves)
    seen: list[dict] = []
    asyncio.run(
        scenarios.write_in_parallel(
            _Contract(), out=tmp_path, wanted=20, rounds=3, on_event=seen.append
        )
    )
    assert calls["n"] == 1, "an approved suite should be reviewed once, not once per round"
    assert not [event for event in seen if event.get("type") == "review_failed"]


def test_an_empty_suite_is_not_reported_as_reviewed(monkeypatch, tmp_path):
    """`gaps_in` returns early when there is nothing to read. That is also 'nobody looked'."""
    review = asyncio.run(
        scenarios.gaps_in(_Contract(), [], destination=tmp_path, wanted=20)
    )
    assert review.reviewed is False and review.complete is False
