"""What a stage spent its turns on, recorded as the run goes.

Reconstructing this from a rendered log afterwards is possible and horrible, and the answer is
what decides whether a slow run is working hard or spinning. These pin the three signals that
told the real story on a live run: repeats, failures, and calls spent per result.
"""

from __future__ import annotations

import json
from pathlib import Path

from fi.alk.harness.session import Event
from fi.alk.harness.trace import Trace


def feed(trace: Trace, *events: Event) -> Trace:
    for one in events:
        trace.record(one)
    return trace


def used(tool: str, target: str = "") -> Event:
    return Event(kind="tool", tool=tool, detail={"target": target})


def came_back(text: str = "ok", failed: bool = False) -> Event:
    return Event(kind="result", text=text, detail={"is_error": failed})


class TestRepeats:
    def test_an_identical_call_is_marked_as_a_repeat(self):
        """60% of calls on a real run were byte-identical repeats. It was invisible in the turn count."""
        trace = feed(
            Trace(name="s"),
            used("Read", "a.py"), came_back(),
            used("Read", "a.py"), came_back(),
            used("Read", "a.py"), came_back(),
        )
        assert trace.repeated == 2
        assert trace.calls[0].repeat_of is None
        assert trace.calls[1].repeat_of == 0
        assert trace.calls[2].repeat_of == 0

    def test_a_different_target_is_not_a_repeat(self):
        trace = feed(Trace(), used("Read", "a.py"), came_back(), used("Read", "b.py"), came_back())
        assert trace.repeated == 0

    def test_the_worst_offender_is_named(self):
        trace = feed(
            Trace(),
            used("Read", "big.py"), came_back(),
            used("Read", "big.py"), came_back(),
            used("Read", "big.py"), came_back(),
            used("Grep", "x"), came_back(),
            used("Grep", "x"), came_back(),
        )
        worst = trace.worst_repeats()
        assert worst[0][0] == "Read|big.py"


class TestFailures:
    def test_an_errored_result_marks_its_call(self):
        trace = feed(Trace(), used("Bash", "python -c 'import psycopg'"), came_back("no module", failed=True))
        assert trace.failures == 1
        assert trace.calls[0].failed

    def test_a_result_with_no_preceding_call_is_ignored_not_fatal(self):
        assert feed(Trace(), came_back()).calls == []


class TestCostPerResult:
    def test_calls_between_artifacts_are_recoverable(self):
        """The cheap run spent 31 calls before its first scenario; the expensive one spent 71."""
        trace = Trace()
        for _ in range(5):
            feed(trace, used("Read", "x"), came_back())
        trace.record(Event(kind="artifact", detail={"path": "/s/one"}))
        feed(trace, used("Read", "y"), came_back())
        trace.record(Event(kind="artifact", detail={"path": "/s/two"}))
        assert [at for at, _ in trace.produced] == [5, 6]
        assert "5->one" in trace.summary()


class TestDurability:
    def test_the_summary_survives_an_empty_run(self):
        assert "no calls" in Trace(name="s").summary()

    def test_it_writes_itself_beside_the_artifacts(self, tmp_path: Path):
        trace = feed(Trace(name="scenarios"), used("Read", "a"), came_back())
        trace.record(Event(kind="done", detail={"turns": 9, "cost_usd": 1.5}))
        path = trace.write(tmp_path)
        held = json.loads(path.read_text())
        assert held["stage"] == "scenarios" and held["turns"] == 9
        assert held["calls"][0]["tool"] == "Read"

    def test_an_unknown_event_kind_is_ignored(self):
        assert feed(Trace(), Event(kind="something-new")).calls == []
