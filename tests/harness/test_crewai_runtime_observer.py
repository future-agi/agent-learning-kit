"""Exercise the standalone observer with the CrewAI 1.6.1 event/bus shape."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


_HARNESS = Path(__file__).resolve().parents[2] / "src/fi/alk/harness"


def _run(tmp_path: Path, script: str, *, marker: bool = True, unwritable: bool = False):
    shutil.copy2(
        _HARNESS / "livekit_tool_trace_bootstrap.py", tmp_path / "sitecustomize.py"
    )
    shutil.copy2(_HARNESS / "crewai_runtime_observer_bootstrap.py", tmp_path)
    trace = tmp_path if unwritable else tmp_path / "calls.jsonl"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONPATH": str(tmp_path),
            "HARNESS_TOOL_TRACE": str(trace),
            "ALK_CREWAI_TOOL_TRACE": "1" if marker else "0",
            "ALK_TOOL_TRACE_BINDINGS": json.dumps(
                [
                    {"name": "lookup", "module": "agent_tools", "callable": "lookup"},
                ]
            ),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    records = (
        [json.loads(line) for line in trace.read_text().splitlines()]
        if trace.is_file()
        else []
    )
    return result, records


def _fake_crewai(root: Path) -> None:
    for directory in ("crewai", "crewai/events", "crewai/events/types"):
        path = root / directory
        path.mkdir(exist_ok=True)
        (path / "__init__.py").write_text("")
    (root / "crewai/events/types/tool_usage_events.py").write_text(
        "from types import SimpleNamespace\n"
        "class ToolUsageFinishedEvent(SimpleNamespace): pass\n"
        "class ToolUsageErrorEvent(SimpleNamespace): pass\n"
        "class ToolUsageStartedEvent(SimpleNamespace): pass\n"
    )
    (root / "crewai/events/event_bus.py").write_text(
        "from concurrent.futures import ThreadPoolExecutor\n"
        "from threading import Event\n"
        "class CrewAIEventsBus:\n"
        "    def __init__(self):\n"
        "        self.executor = ThreadPoolExecutor(1)\n"
        "        self.release = Event()\n"
        "        self.calls = 0\n"
        "    def emit(self, source, event):\n"
        "        self.calls += 1\n"
        "        if source is not None: raise source\n"
        "        if getattr(event, 'no_handlers', False): return None\n"
        "        self.future = self.executor.submit(self.release.wait)\n"
        "        return self.future\n"
    )
    (root / "agent_tools.py").write_text("def lookup(query): return 'found ' + query\n")


_SCRIPT = """
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from crewai.events.event_bus import CrewAIEventsBus
from crewai.events.types.tool_usage_events import (
    ToolUsageFinishedEvent, ToolUsageErrorEvent, ToolUsageStartedEvent,
)
import crewai_runtime_observer_bootstrap as observer
from agent_tools import lookup
assert observer.install()
wrapped = CrewAIEventsBus.emit
assert observer.install() and CrewAIEventsBus.emit is wrapped
bus = CrewAIEventsBus()
now = datetime.now(timezone.utc)
fields = dict(tool_name='lookup', tool_args={'query': 'Paris'}, timestamp=now,
              agent_id='worker-1', agent_key='researcher', agent_role='Researcher',
              task_id='task-1', task_name='Find weather', agent=object(), from_task=object())
try:
    result = lookup('Paris')
    assert result == 'found Paris'
    event = ToolUsageFinishedEvent(**fields, output=result, started_at=now,
                                  finished_at=now, from_cache=True)
    returned = bus.emit(None, event)
    assert returned is bus.future and not returned.done()
    event = ToolUsageErrorEvent(**fields, error=ValueError('no match'))
    assert bus.emit(None, event) is bus.future
    bus.emit(None, ToolUsageStartedEvent(**fields))
    assert bus.emit(None, ToolUsageStartedEvent(**fields, no_handlers=True)) is None
    bus.emit(None, ToolUsageFinishedEvent(tool_name=None, tool_args=object()))
    error = RuntimeError('original failure')
    try:
        bus.emit(error, event)
    except RuntimeError as actual:
        assert actual is error
    else:
        raise AssertionError('emit exception swallowed')
    assert bus.calls == 6
    trace = Path(os.environ['HARNESS_TOOL_TRACE'])
    if trace.is_file():
        assert len(trace.read_text().splitlines()) == 2
    print(result)
finally:
    bus.release.set()
    bus.executor.shutdown()
"""


@pytest.mark.parametrize("unwritable", [False, True])
def test_terminal_events_preserve_emit_and_customer_tools(
    tmp_path: Path, unwritable: bool
) -> None:
    _fake_crewai(tmp_path)
    result, records = _run(tmp_path, _SCRIPT, unwritable=unwritable)
    assert result.stdout.strip() == "found Paris"
    assert result.stderr == ""
    if unwritable:
        assert not records
        return
    success, error = records
    assert success == {
        "name": "lookup",
        "arguments": {"query": "Paris"},
        "output": "found Paris",
        "is_error": False,
        "at": success["at"],
        "source": "crewai",
        "from_cache": True,
        "agent_id": "worker-1",
        "agent_key": "researcher",
        "agent_role": "Researcher",
        "task_id": "task-1",
        "task_name": "Find weather",
    }
    assert error == {
        **{key: value for key, value in success.items() if key != "from_cache"},
        "is_error": True,
        "output": "no match",
    }


def test_finished_event_with_framework_failure_is_an_error(tmp_path: Path) -> None:
    _fake_crewai(tmp_path)
    script = """
from crewai.events.event_bus import CrewAIEventsBus
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent
bus = CrewAIEventsBus()
bus.emit(None, ToolUsageFinishedEvent(
    tool_name='lookup', tool_args={'query': 'Paris'}, output='ignored',
    failure='provider rejected the call', no_handlers=True))
"""
    _result, records = _run(tmp_path, script)
    assert records[0]["is_error"] is True
    assert records[0]["output"] == "provider rejected the call"


def test_missing_crewai_does_not_fabricate_generic_tool_evidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "crewai.py").write_text("raise ImportError('CrewAI unavailable')\n")
    (tmp_path / "agent_tools.py").write_text(
        "def lookup(query): raise ValueError('lookup failed')\n"
    )
    result, records = _run(
        tmp_path,
        "from agent_tools import lookup\ntry:\n lookup('Paris')\nexcept ValueError:\n print('failed')",
    )
    assert result.stdout.strip() == "failed"
    assert records == []


def test_disabled_crewai_observer_keeps_generic_trace(tmp_path: Path) -> None:
    _fake_crewai(tmp_path)
    (tmp_path / "agent_tools.py").write_text(
        "def lookup(query): return 'found ' + query\n"
    )
    result, records = _run(
        tmp_path,
        "from agent_tools import lookup; print(lookup('Paris'))",
        marker=False,
    )
    assert result.stdout.strip() == "found Paris"
    assert len(records) == 1
    assert records[0]["name"] == "lookup"
    assert records[0]["output"] == "found Paris"


def test_trace_payload_is_bounded_and_never_serializes_agent_objects(
    tmp_path: Path,
) -> None:
    _fake_crewai(tmp_path)
    script = """
from crewai.events.event_bus import CrewAIEventsBus
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent
class PrivateObject:
    def __str__(self): raise AssertionError('private object serialized')
bus = CrewAIEventsBus()
try:
    private = PrivateObject()
    event = ToolUsageFinishedEvent(
        tool_name='lookup', tool_args={'query': 'x' * 100000},
        output={'result': ['x' * 100000] * 100000, 'private': private},
        agent=private, from_task=private, from_agent=private,
        task_name='t' * 10000, agent_role='r' * 10000,
        event_id='real-id', parent_event_id='parent-id',
    )
    assert bus.emit(None, event) is bus.future
finally:
    bus.release.set()
    bus.executor.shutdown()
"""
    _, records = _run(tmp_path, script)
    assert len(records) == 1
    record = records[0]
    assert len(json.dumps(record)) <= 65536
    assert len(record["task_name"]) == len(record["agent_role"]) == 256
    assert record["event_id"] == "real-id"
    assert record["parent_event_id"] == "parent-id"
    assert "started_event_id" not in record
    assert not {"agent", "from_task", "from_agent"}.intersection(record)
