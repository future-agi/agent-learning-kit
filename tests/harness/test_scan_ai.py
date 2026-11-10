"""The AI step of the agent scan: one paid turn, what it ends as, and the tools the model reads with.

No test calls a model. A fake backend plays a script: what the model says, which tools it calls
(through the real handlers), and how its turn ends. Every test that waits on a stage runs under
its own bound, so a missing bound in the code fails the test instead of hanging the run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from fi.alk.harness import scan_ai, session, spend
from fi.alk.harness.backends import Call, ModelReply, Say, StageDone, ToolReturned
from fi.alk.harness.config import load_skill

GEMINI = "gemini-fake-model"
SUBMIT = "mcp__scan__submit_agents"
READ = "mcp__scan_files__read"
GLOB = "mcp__scan_files__glob"
GREP = "mcp__scan_files__grep"
AGENT = ("services/support", "services/support/agent.py")
KEPT = ("call", SUBMIT, {"agents": [{"folder": AGENT[0], "entry": AGENT[1]}]})
FINISHED = ("done", StageDone(outcome="success", turns=3, cost_usd=0.01, models={GEMINI}))
# ADK's whole message, as the Vertex-Gemini backend passes it on.
CALL_LIMIT_MESSAGE = "Max number of llm calls limit of `40` exceeded"
TEST_BOUND = 15

# Read before any test shortens them.
_BOUNDS = {
    name: getattr(scan_ai, name)
    for name in (
        "OPEN_SECONDS",
        "INTERRUPT_SECONDS",
        "SPEND_WAIT_SECONDS",
        "EXIT_SECONDS",
        "TOOL_SECONDS",
        "IDLE_SECONDS",
        "MAX_TURNS",
    )
}


@pytest.fixture(autouse=True)
def _clean_ledger(monkeypatch):
    retries = session.STAGE_IDLE_RETRIES
    monkeypatch.delenv("ALK_SPEND_JOURNAL", raising=False)
    monkeypatch.setattr(spend, "_path", None)
    monkeypatch.setenv("ALK_HARNESS_MODEL", GEMINI)
    yield
    session.STAGE_IDLE_RETRIES = retries
    spend._stages.clear()


@pytest.fixture
def quick(monkeypatch):
    for name, seconds in (
        ("OPEN_SECONDS", 0.3),
        ("INTERRUPT_SECONDS", 0.2),
        ("SPEND_WAIT_SECONDS", 0.3),
        ("CANCEL_SECONDS", 0.2),
        ("EXIT_SECONDS", 0.2),
    ):
        monkeypatch.setattr(scan_ai, name, seconds)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _repo(tmp_path: Path) -> Path:
    source = tmp_path / "repo"
    _write(source / AGENT[1], "from openai import OpenAI\nclient = OpenAI()\n")
    _write(source / "services/support/pyproject.toml", "[project]\nname = 'support'\n")
    _write(source / "main.py", "print('hello')\n")
    _write(source / "tests/test_agent.py", "def test_nothing():\n    pass\n")
    return source


def _said(result: dict[str, Any]) -> str:
    return "".join(part.get("text", "") for part in result.get("content", []))


class _Backend:
    """Sessions that play ``steps`` instead of calling a model, and count what they were asked."""

    name = "fake"
    default_model = GEMINI

    def __init__(self, *steps: tuple, start: str = "", stop: str = "", interrupt: str = ""):
        self.steps = steps
        self.start = start
        self.stop = stop
        self.interrupt = interrupt
        self.specs: list[Any] = []
        self.sent: list[str] = []
        self.results: list[dict[str, Any]] = []
        self.interrupts = 0
        self.stops = 0

    def create(self, spec):
        self.specs.append(spec)
        return _Session(self, spec)

    def can_drive(self, model: str) -> bool:
        return True


class _Session:
    def __init__(self, backend: _Backend, spec) -> None:
        self.backend = backend
        self.spec = spec
        self.interrupted = asyncio.Event()

    async def start(self) -> None:
        if self.backend.start == "hang":
            await asyncio.Event().wait()
        if self.backend.start == "raise":
            raise RuntimeError("the provider refused the session")

    async def stop(self) -> None:
        self.backend.stops += 1
        if self.backend.stop == "hang":
            await asyncio.Event().wait()

    async def send(self, message: str) -> None:
        self.backend.sent.append(message)

    async def interrupt(self) -> bool:
        self.backend.interrupts += 1
        if self.backend.interrupt == "hang":
            await asyncio.Event().wait()
        self.interrupted.set()
        return True

    async def resume(self, invocation_id: str) -> None:
        raise RuntimeError("not supported")

    def _handler(self, qualified: str):
        _, server, name = qualified.split("__")
        return next(
            spec.handler for spec in self.spec.servers[server].tools if spec.name == name
        )

    async def replies(self):
        for step in self.backend.steps:
            kind = step[0]
            if kind == "say":
                yield ModelReply(parts=[Say(text=step[1])], model=GEMINI)
            elif kind == "call":
                call_id = f"call-{len(self.backend.results)}"
                yield ModelReply(parts=[Call(id=call_id, name=step[1], arguments=step[2])])
                result = await self._handler(step[1])(step[2])
                self.backend.results.append(result)
                yield ToolReturned(
                    id=call_id, text=_said(result), is_error=bool(result.get("is_error"))
                )
            elif kind == "done":
                yield step[1]
            elif kind == "quiet":
                await asyncio.Event().wait()
            elif kind == "until_interrupt":
                await self.interrupted.wait()
            elif kind == "raise":
                raise step[1]


def _scan(monkeypatch, source: Path, backend: _Backend, *, seconds: float = 5.0,
          hints: str = "3 Python files outside side folders", ends: list | None = None):
    monkeypatch.setattr(session, "resolve", lambda *_args, **_kwargs: backend)
    noted = ends if ends is not None else []
    return asyncio.run(
        asyncio.wait_for(
            scan_ai.run_ai_step(
                source, hints=hints, seconds=seconds, on_turn_end=lambda: noted.append(1)
            ),
            TEST_BOUND,
        )
    )


@dataclass
class _Case:
    steps: tuple
    kind: str
    retryable: bool = False
    complete: bool = True
    ends: int = 1
    seconds: float = 5.0
    interrupt: str = ""
    idle: float | None = None
    pairs: tuple = field(default_factory=tuple)


def _done(**facts: Any) -> tuple:
    facts.setdefault("models", {GEMINI})
    return ("done", StageDone(**facts))


_OUTCOMES = {
    "pairs": _Case((KEPT, FINISHED), "pairs", pairs=(AGENT,)),
    "pairs_then_a_forbidden_model": _Case(
        (KEPT, _done(models={"claude-sonnet-4-6"})), "pairs", pairs=(AGENT,)
    ),
    "forbidden": _Case((_done(models={"claude-sonnet-4-6"}),), "forbidden"),
    "timed_out_quiet": _Case((("quiet",),), "timed_out", complete=False, ends=0, seconds=0.3),
    "timed_out_bill_after_the_interrupt": _Case(
        (("until_interrupt",), FINISHED), "timed_out", complete=False, seconds=0.3
    ),
    "interrupt_never_returns": _Case(
        (("quiet",),), "timed_out", complete=False, ends=0, seconds=0.3, interrupt="hang"
    ),
    "idle_stream": _Case((("quiet",),), "model_error", True, complete=False, ends=0, idle=0.2),
    "say_raises": _Case(
        (("raise", RuntimeError("stream broke")),), "model_error", True, complete=False, ends=0
    ),
    "status_400": _Case(
        (_done(outcome="failed", is_error=True, api_error_status=400),), "model_error", False
    ),
    "status_408": _Case(
        (_done(outcome="failed", is_error=True, api_error_status=408),), "model_error", True
    ),
    "status_409": _Case(
        (_done(outcome="failed", is_error=True, api_error_status=409),), "model_error", True
    ),
    "status_429": _Case(
        (_done(outcome="failed", is_error=True, api_error_status=429),), "model_error", True
    ),
    "status_500": _Case(
        (_done(outcome="failed", is_error=True, api_error_status=500),), "model_error", True
    ),
    "is_error_without_status": _Case(
        (_done(outcome="failed", is_error=True, turns=3),), "model_error", True
    ),
    "is_error_without_status_on_the_last_turn": _Case(
        (
            _done(
                outcome="failed",
                is_error=True,
                api_error_status=None,
                turns=40,
                errors=["The connection was reset by the server."],
            ),
        ),
        "model_error",
        True,
    ),
    "call_limit_words_inside_other_text": _Case(
        (
            _done(
                outcome="failed",
                is_error=True,
                api_error_status=None,
                turns=7,
                errors=[f"Retry failed: {CALL_LIMIT_MESSAGE} (upstream)"],
            ),
        ),
        "model_error",
        True,
    ),
    "call_limit_words_without_a_number": _Case(
        (
            _done(
                outcome="failed",
                is_error=True,
                api_error_status=None,
                turns=7,
                errors=["Max number of llm calls limit of `` exceeded"],
            ),
        ),
        "model_error",
        True,
    ),
    "claude_turn_limit": _Case(
        (_done(outcome="error_max_turns", is_error=True, turns=40),), "no_answer"
    ),
    "gemini_max_turns": _Case(
        (_done(outcome="max_turns", is_error=True, turns=12),), "no_answer"
    ),
    "gemini_call_limit": _Case(
        (
            _done(
                outcome="failed",
                is_error=True,
                api_error_status=None,
                turns=40,
                errors=[CALL_LIMIT_MESSAGE],
            ),
        ),
        "no_answer",
    ),
    "gemini_call_limit_in_adks_words": _Case(
        (
            _done(
                outcome="failed",
                is_error=True,
                api_error_status=None,
                turns=7,
                errors=[CALL_LIMIT_MESSAGE],
            ),
        ),
        "no_answer",
    ),
    "empty": _Case((("call", SUBMIT, {"agents": []}), FINISHED), "empty"),
    "every_pair_refused": _Case(
        (("call", SUBMIT, {"agents": [{"folder": "", "entry": "missing.py"}]}), FINISHED),
        "empty",
    ),
    "no_answer": _Case((("say", "I looked around."), FINISHED), "no_answer"),
}


@pytest.mark.parametrize("name", sorted(_OUTCOMES))
def test_each_ai_outcome_gives_its_result(tmp_path, monkeypatch, quick, name):
    case = _OUTCOMES[name]
    if case.idle is not None:
        monkeypatch.setattr(scan_ai, "IDLE_SECONDS", case.idle)
    session.STAGE_IDLE_RETRIES = 1
    backend = _Backend(*case.steps, interrupt=case.interrupt)
    ends: list = []

    outcome = _scan(monkeypatch, _repo(tmp_path), backend, seconds=case.seconds, ends=ends)

    assert outcome.kind == case.kind
    assert outcome.pairs == case.pairs
    assert outcome.complete is case.complete
    assert outcome.retryable is case.retryable
    assert len(ends) == case.ends
    # The scan's stage turns replay off for itself, never for the whole process.
    assert session.STAGE_IDLE_RETRIES == 1
    assert len(backend.sent) == 1
    if case.kind == "timed_out":
        assert backend.interrupts == 1


def test_a_gemini_call_limit_is_no_answer(tmp_path, monkeypatch):
    """The real Vertex-Gemini backend and ADK, with only the model scripted: it never submits."""
    pytest.importorskip("google.adk")
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from fi.alk.harness.backends import vertex_gemini

    calls: list[int] = []

    class LoopingLlm(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            calls.append(1)
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name=GLOB, args={"pattern": "*.py"}, id=f"c{len(calls)}"
                            )
                        )
                    ],
                ),
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=1000, candidates_token_count=10
                ),
            )

    # The backend writes these three itself; set here so they are put back afterwards.
    for name, value in (
        ("GOOGLE_CLOUD_PROJECT", "fake-project"),
        ("GOOGLE_CLOUD_LOCATION", "global"),
        ("GOOGLE_GENAI_USE_VERTEXAI", "TRUE"),
        ("ALK_HARNESS_MODEL", "gemini-2.5-flash"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AGENTCC_API_KEY", raising=False)
    monkeypatch.setattr(vertex_gemini, "COMPACT_ABOVE_TOKENS", 0)
    monkeypatch.setattr(vertex_gemini, "_llm", lambda model: LoopingLlm(model=model))
    for name, seconds in (
        ("INTERRUPT_SECONDS", 0.2),
        ("SPEND_WAIT_SECONDS", 0.3),
        ("CANCEL_SECONDS", 0.2),
        ("EXIT_SECONDS", 1.0),
        ("OPEN_SECONDS", 5.0),
    ):
        monkeypatch.setattr(scan_ai, name, seconds)
    ends: list = []

    outcome = _scan(
        monkeypatch,
        _repo(tmp_path),
        vertex_gemini.VertexGeminiBackend(),
        seconds=8.0,
        ends=ends,
    )

    assert outcome.kind == "no_answer"
    assert outcome.complete is True
    assert len(ends) == 1
    assert 1 <= len(calls) <= scan_ai.MAX_TURNS + 1
    assert "llm calls" not in outcome.message.lower()


def test_an_idle_ai_stream_is_not_replayed(tmp_path, monkeypatch, quick):
    session.STAGE_IDLE_RETRIES = 1
    monkeypatch.setattr(scan_ai, "IDLE_SECONDS", 0.2)
    backend = _Backend(("quiet",))

    outcome = _scan(monkeypatch, _repo(tmp_path), backend)

    assert outcome.kind == "model_error"
    assert outcome.retryable is True
    assert len(backend.specs) == 1
    assert len(backend.sent) == 1
    # The scan's stage turns replay off for itself, never for the whole process.
    assert session.STAGE_IDLE_RETRIES == 1


def test_pairs_submitted_before_an_idle_stream_are_kept(tmp_path, monkeypatch, quick):
    monkeypatch.setattr(scan_ai, "IDLE_SECONDS", 0.2)
    backend = _Backend(KEPT, ("quiet",))

    outcome = _scan(monkeypatch, _repo(tmp_path), backend)

    assert outcome.kind == "pairs"
    assert outcome.pairs == (AGENT,)
    assert outcome.complete is False


# A model that ends without submitting is the turn a reminder would be tempted to follow.
@pytest.mark.parametrize(
    ("steps", "kind"),
    [((("say", "Looking."), KEPT, FINISHED), "pairs"), ((("say", "Looking."), FINISHED), "no_answer")],
    ids=["submitted", "ended_without_submitting"],
)
def test_the_stage_says_exactly_once(tmp_path, monkeypatch, quick, steps, kind):
    backend = _Backend(*steps)
    ends: list = []
    hints = "3 Python files outside side folders"

    outcome = _scan(monkeypatch, _repo(tmp_path), backend, hints=hints, ends=ends)

    assert outcome.kind == kind
    assert len(backend.specs) == 1
    assert backend.sent == [scan_ai._opening(hints)]
    assert len(ends) == 1


def test_complete_is_false_after_the_stop_sequence(tmp_path, monkeypatch, quick):
    assert _BOUNDS["INTERRUPT_SECONDS"] == 10
    assert _BOUNDS["SPEND_WAIT_SECONDS"] == 60
    backend = _Backend(KEPT, ("until_interrupt",), FINISHED)
    ends: list = []

    outcome = _scan(monkeypatch, _repo(tmp_path), backend, seconds=0.3, ends=ends)

    assert outcome.kind == "pairs"
    assert backend.interrupts == 1
    # The bill arrived during the wait, and still the turn may be missing a call in flight.
    assert len(ends) == 1
    assert outcome.complete is False


@pytest.mark.parametrize("start", ["hang", "raise"])
def test_a_stage_that_will_not_open_is_a_model_error(tmp_path, monkeypatch, quick, start):
    assert _BOUNDS["OPEN_SECONDS"] == 30
    backend = _Backend(KEPT, FINISHED, start=start)

    outcome = _scan(monkeypatch, _repo(tmp_path), backend)

    assert outcome.kind == "model_error"
    assert outcome.retryable is True
    assert outcome.complete is False
    assert backend.sent == []


def test_a_stage_that_will_not_stop_is_left_after_ten_seconds(tmp_path, monkeypatch, quick):
    assert _BOUNDS["EXIT_SECONDS"] == 10
    backend = _Backend(KEPT, FINISHED, stop="hang")

    outcome = _scan(monkeypatch, _repo(tmp_path), backend)

    assert outcome.kind == "pairs"
    assert outcome.complete is True
    assert backend.stops == 1


def test_the_opening_carries_no_path_and_the_outcome_no_model_text(tmp_path, monkeypatch, quick):
    source = _repo(tmp_path)
    hints = "2 Python files outside side folders; 1 candidate in side folders"
    model_text = "MODEL-TEXT services/support/agent.py says sk-fake-123"
    assert "/" not in scan_ai._opening("")

    failed = _Backend(
        ("say", model_text),
        _done(outcome="failed", is_error=True, turns=2, errors=[model_text]),
    )
    outcome = _scan(monkeypatch, source, failed, hints=hints)
    assert outcome.kind == "model_error"
    assert failed.sent == [scan_ai._opening(hints)]
    assert failed.sent[0].endswith(hints)
    for path in (str(source), str(tmp_path), "services/", "tests/", "agent.py"):
        assert path not in failed.sent[0]

    raised = _Backend(("raise", RuntimeError(model_text)),)
    raised_outcome = _scan(monkeypatch, source, raised, hints=hints)
    assert raised_outcome.message == "the model step failed (RuntimeError)"

    answered = _Backend(("say", model_text), KEPT, FINISHED)
    kept = _scan(monkeypatch, source, answered, hints=hints)
    assert kept.pairs == (AGENT,)

    for one in (outcome, raised_outcome, kept):
        assert "MODEL-TEXT" not in one.message
        assert "sk-fake-123" not in one.message


def test_the_stage_has_no_built_in_tools(tmp_path, monkeypatch, quick):
    source = _repo(tmp_path)
    backend = _Backend(FINISHED)

    _scan(monkeypatch, source, backend)

    spec = backend.specs[0]
    assert spec.builtins == ()
    assert spec.ask is None
    assert spec.gated is True
    assert spec.thinking is False
    assert spec.max_turns == 40
    assert spec.idle_timeout_seconds == 90
    assert spec.cwd == str(source)
    assert spec.model == GEMINI
    assert spec.system_prompt == load_skill("find-agents")
    assert set(spec.servers) == {"scan_files", "scan"}
    assert sorted(spec.granted()) == sorted([READ, GLOB, GREP, SUBMIT])
    for name in (READ, GLOB, GREP, SUBMIT):
        assert name in spec.system_prompt


def test_the_skills_description_says_when_the_model_is_asked():
    # The model reads the whole file, its description included.
    skill = load_skill("find-agents", preamble=False)
    description = yaml.safe_load(skill.split("---\n")[1])["description"]

    assert "came back empty" not in skill
    assert "found no agent outside test and example folders" in description
    assert "apart from any files that only call a model" in description
    assert "TypeScript or JavaScript" in description
    assert skill in load_skill("find-agents")


# -- the tools ---------------------------------------------------------------------------------


def _tools(root: Path) -> dict[str, Any]:
    return {spec.name: spec.handler for spec in scan_ai.rooted_file_tools(root).tools}


def _call(handler, arguments: Any) -> dict[str, Any]:
    return asyncio.run(asyncio.wait_for(handler(arguments), TEST_BOUND))


def _listed(text: str) -> set[str]:
    """The paths a glob or grep result names."""
    return {line.split(":", 1)[0] for line in text.splitlines() if line}


def test_the_ai_cannot_read_outside_the_source(tmp_path):
    outside = _write(tmp_path / "outside/creds.txt", "outside-needle\n")
    source = tmp_path / "repo"
    _write(source / "inside.py", "inside-needle\n")
    os.symlink(outside.parent, source / "out_link")
    os.symlink(outside, source / "file_link.py")
    tools = _tools(source)

    for path in (
        "../outside/creds.txt",
        str(outside),
        "out_link/creds.txt",
        "file_link.py",
        "inside.py/../../outside/creds.txt",
    ):
        result = _call(tools["read"], {"path": path})
        assert result.get("is_error"), path
        assert "outside-needle" not in _said(result)
    for path in ("../outside", "/", str(tmp_path), "out_link"):
        assert _call(tools["glob"], {"pattern": "*", "path": path}).get("is_error"), path
        assert _call(tools["grep"], {"text": "needle", "path": path}).get("is_error"), path

    assert _listed(_said(_call(tools["glob"], {"pattern": "*"}))) == {"inside.py"}
    found = _said(_call(tools["grep"], {"text": "needle"}))
    assert "outside-needle" not in found
    assert _listed(found) == {"inside.py"}
    assert "inside-needle" in _said(_call(tools["read"], {"path": str(source / "inside.py")}))


_HIDDEN = (
    ".env",
    "config/service_account.json",
    "config/token.json",
    "prod.env",
    "app.env.local",
    "secrets.toml",
    "credentials.yml",
    "id_rsa",
    "terraform.tfvars",
    "secrets/keys.py",
)


def test_grep_and_glob_never_show_a_secret_file(tmp_path):
    source = tmp_path / "repo"
    _write(source / "agent.py", "needle = 1\n")
    _write(source / "config/settings.py", "needle = 2\n")
    for name in _HIDDEN:
        _write(source / name, "needle API_KEY=sk-fake-123\n")
    tools = _tools(source)

    for path in (None, ".", "config", str(source), str(source / "config")):
        arguments = {} if path is None else {"path": path}
        for grep in ({"text": "needle"}, {"text": "NEEDLE", "ignore_case": True}):
            found = _said(_call(tools["grep"], {**grep, **arguments}))
            assert "sk-fake-123" not in found
            assert not _listed(found) & set(_HIDDEN), (path, found)
            assert _listed(found) <= {"agent.py", "config/settings.py"}
        for pattern in ("*", "**", "*.env", "*.json", "id_*", "*secret*"):
            listed = _said(_call(tools["glob"], {"pattern": pattern, **arguments}))
            assert not _listed(listed) & set(_HIDDEN), (path, pattern, listed)
    assert "agent.py" in _listed(_said(_call(tools["grep"], {"text": "needle"})))
    for name in _HIDDEN:
        result = _call(tools["read"], {"path": name})
        assert result.get("is_error"), name
        assert "sk-fake-123" not in _said(result)


def test_the_tools_skip_fifos_and_big_files_and_hard_links(tmp_path):
    source = tmp_path / "repo"
    _write(source / "agent.py", "needle\n")
    os.mkfifo(source / "pipe.py")
    _write(source / "big.py", "needle\n" * 300_000)
    _write(source / "linked.py", "needle\n")
    os.link(source / "linked.py", source / "also.py")
    os.link(_write(tmp_path / "outside.py", "needle\n"), source / "hard.py")
    _write(source / "app.min.js", "needle\n")
    _write(source / "long.py", "needle" + "x" * 70_000 + "\nneedle\n")
    tools = _tools(source)

    assert _listed(_said(_call(tools["glob"], {"pattern": "*"}))) == {"agent.py", "long.py"}
    found = _said(_call(tools["grep"], {"text": "needle"}))
    assert found.splitlines() == ["agent.py:1:needle", "long.py:2:needle"]
    for name in ("pipe.py", "big.py", "linked.py", "also.py", "hard.py"):
        assert _call(tools["read"], {"path": name}).get("is_error"), name
        assert _call(tools["grep"], {"text": "needle", "path": name}).get("is_error"), name


def test_a_slow_tool_call_ends_with_an_error(tmp_path, monkeypatch):
    assert _BOUNDS["TOOL_SECONDS"] == 20
    source = _repo(tmp_path)
    monkeypatch.setattr(scan_ai, "TOOL_SECONDS", 0.2)
    monkeypatch.setattr(scan_ai, "_grep_files", lambda root, arguments: time.sleep(3) or "late")
    tools = _tools(source)

    began = time.monotonic()
    result = _call(tools["grep"], {"text": "needle"})

    assert time.monotonic() - began < 2
    assert result.get("is_error")
    assert "late" not in _said(result)


def test_no_tool_raises(tmp_path, monkeypatch):
    source = _repo(tmp_path)
    tools = _tools(source)
    calls = [
        ("read", {"path": "main\x00.py"}),
        ("read", {"path": "main.py", "offset": "x"}),
        ("read", {"path": "main.py", "limit": [1]}),
        ("read", {"path": "/".join(["a"] * 3_000)}),
        ("read", {"path": "a" * 5_000}),
        ("read", {"path": 7}),
        ("read", {}),
        ("read", None),
        ("glob", {"pattern": "x" * 300}),
        ("glob", {"pattern": None}),
        ("glob", {"pattern": "*", "path": "\x00"}),
        ("grep", {"text": ""}),
        ("grep", {"text": "x" * 201}),
        ("grep", {"text": "a", "ignore_case": "yes"}),
        ("grep", {"text": "a", "glob": 5}),
        ("grep", {"text": "a", "path": "missing/folder"}),
    ]
    for name, arguments in calls:
        assert _call(tools[name], arguments).get("is_error"), (name, arguments)

    def broken(*_args):
        raise RuntimeError("MODEL-TEXT from inside a worker")

    for worker in ("_read_lines", "_glob_files", "_grep_files", "_checked_pairs"):
        monkeypatch.setattr(scan_ai, worker, broken)
    for name, arguments in (
        ("read", {"path": "main.py"}),
        ("glob", {"pattern": "*"}),
        ("grep", {"text": "a"}),
    ):
        result = _call(tools[name], arguments)
        assert result.get("is_error")
        assert _said(result) == scan_ai._FAILED
    submit = scan_ai._submit_server(source, scan_ai._Answer()).tools[0].handler
    submitted = _call(submit, {"agents": [{"folder": "", "entry": "main.py"}]})
    assert submitted.get("is_error")
    assert _said(submitted) == scan_ai._FAILED


def test_tool_paths_are_relative(tmp_path):
    source = tmp_path / "repo"
    _write(source / "pkg/agent.py", "needle\n")
    tools = _tools(source)

    for path in (None, "pkg", str(source / "pkg")):
        arguments = {} if path is None else {"path": path}
        listed = _said(_call(tools["glob"], {"pattern": "*.py", **arguments}))
        found = _said(_call(tools["grep"], {"text": "needle", **arguments}))
        assert listed == "pkg/agent.py"
        assert found == "pkg/agent.py:1:needle"
        for text in (listed, found):
            assert str(tmp_path) not in text
    assert _said(_call(tools["read"], {"path": "pkg/agent.py"})) == "1\tneedle"


def test_a_capped_search_says_it_stopped_early(tmp_path):
    source = tmp_path / "repo"
    for index in range(2_100):
        _write(source / f"many/f{index:04}.txt", "")
    tools = _tools(source)

    for name, arguments in (
        ("glob", {"pattern": "*.nothing"}),
        ("grep", {"text": "nothing-here"}),
    ):
        said = _said(_call(tools[name], arguments))
        assert said == scan_ai.STOPPED_EARLY, (name, said)

    full = tmp_path / "full"
    _write(full / "hits.py", "".join(f"needle {index}\n" for index in range(2_000)))
    _write(full / "wide.py", "".join(f"{'y' * 100}\n" for _ in range(400)))
    full_tools = _tools(full)
    for name, arguments in (
        ("grep", {"text": "needle"}),
        ("read", {"path": "wide.py"}),
    ):
        said = _said(_call(full_tools[name], arguments))
        assert said.endswith(scan_ai.STOPPED_EARLY), name
        assert len(said) <= 8_000, name


def test_one_long_hit_does_not_hide_the_others(tmp_path):
    source = tmp_path / "repo"
    _write(source / "a.py", "needle" + "x" * 20_000 + "\n")
    for name in ("b.py", "c.py", "d.py", "e.py"):
        _write(source / name, "needle\n")
    tools = _tools(source)

    found = _said(_call(tools["grep"], {"text": "needle"}))

    assert _listed(found) == {"a.py", "b.py", "c.py", "d.py", "e.py"}
    assert scan_ai.STOPPED_EARLY not in found
    for line in found.splitlines():
        assert len(line.split(":", 2)[2]) <= 300


# -- the answer --------------------------------------------------------------------------------


def _submit(source: Path, agents: Any) -> tuple[str, Any]:
    answer = scan_ai._Answer()
    handler = scan_ai._submit_server(source, answer).tools[0].handler
    return _said(_call(handler, {"agents": agents})), answer


def test_the_models_paths_are_checked(tmp_path):
    source = _repo(tmp_path)
    _write(source / "node_modules/pkg/agent.js", "x\n")
    _write(source / "build/agent.py", "x\n")
    _write(source / "services/other/run.py", "x\n")
    evil = _write(tmp_path / "outside/evil.py", "x\n")
    os.symlink(evil, source / "services/support/evil.py")
    os.symlink(evil.parent, source / "linked")

    for folder, entry, kept in (
        ("", "main.py", ("", "main.py")),
        ("", AGENT[1], ("", AGENT[1])),
        (AGENT[0], AGENT[1], AGENT),
        ("./" + AGENT[0], "./" + AGENT[1], AGENT),
        (f"{source}/{AGENT[0]}", f"{source}/{AGENT[1]}", AGENT),
        (str(source), f"{source}/main.py", ("", "main.py")),
    ):
        said, answer = _submit(source, [{"folder": folder, "entry": entry}])
        assert said.startswith("Recorded 1; 0 refused."), (folder, entry, said)
        assert answer.pairs == (kept,)

    for agent in (
        {"folder": "services/other", "entry": AGENT[1]},
        {"folder": "services/sup", "entry": AGENT[1]},
        {"folder": "", "entry": "../outside/evil.py"},
        {"folder": "", "entry": str(evil)},
        {"folder": AGENT[0], "entry": "services/support/evil.py"},
        {"folder": "linked", "entry": "linked/evil.py"},
        {"folder": "", "entry": AGENT[0]},
        {"folder": "", "entry": "missing.py"},
        {"folder": "", "entry": "node_modules/pkg/agent.js"},
        {"folder": "", "entry": "build/agent.py"},
        {"folder": "", "entry": "main.py\n"},
        {"folder": "", "entry": ""},
        {"folder": "../", "entry": "main.py"},
        {"folder": 1, "entry": "main.py"},
        "main.py",
    ):
        said, answer = _submit(source, [agent])
        assert said.startswith("Recorded 0; 1 refused."), (agent, said)
        assert answer.pairs == ()
        assert answer.called is True

    said, answer = _submit(source, [{"folder": "", "entry": "main.py"}] * 2)
    assert said.startswith("Recorded 1; 0 refused.")
    assert answer.pairs == (("", "main.py"),)

    said, answer = _submit(source, [{"folder": "", "entry": "main.py"}] * 51)
    assert answer.pairs == ()
    assert answer.called is False


def test_the_first_kept_submit_is_final_and_the_tools_stop(tmp_path, monkeypatch, quick):
    backend = _Backend(
        ("call", SUBMIT, {"agents": [{"folder": "", "entry": "missing.py"}]}),
        KEPT,
        ("call", SUBMIT, {"agents": [{"folder": "", "entry": "main.py"}]}),
        ("call", READ, {"path": "main.py"}),
        ("call", GLOB, {"pattern": "*"}),
        ("call", GREP, {"text": "print"}),
        FINISHED,
    )

    outcome = _scan(monkeypatch, _repo(tmp_path), backend)

    said = [_said(result) for result in backend.results]
    assert said[0].startswith("Recorded 0; 1 refused.")
    assert said[1] == "Recorded 1; 0 refused. Your work is done: end your turn now."
    assert said[2] == "Already submitted."
    assert said[3:] == ["Already submitted: end your turn."] * 3
    assert outcome.kind == "pairs"
    assert outcome.pairs == (AGENT,)
    assert outcome.refused == 0


def test_a_refused_pair_in_the_kept_submit_is_counted(tmp_path, monkeypatch, quick):
    agents = [
        {"folder": AGENT[0], "entry": AGENT[1]},
        {"folder": "", "entry": "missing.py"},
        {"folder": "services/other", "entry": AGENT[1]},
    ]
    backend = _Backend(("call", SUBMIT, {"agents": agents}), FINISHED)

    outcome = _scan(monkeypatch, _repo(tmp_path), backend)

    assert _said(backend.results[0]).startswith("Recorded 1; 2 refused.")
    assert outcome.kind == "pairs"
    assert outcome.pairs == (AGENT,)
    assert outcome.refused == 2


def test_an_entry_reached_through_a_link_out_of_the_source_is_refused(tmp_path):
    source = _repo(tmp_path)
    _write(tmp_path / "outside/other.py", "x\n")
    # A link in the middle of the path: the entry itself is a regular file, and its folder is
    # inside the source.
    os.symlink(tmp_path / "outside", source / "services/support/ext")

    for folder in (AGENT[0], ""):
        said, answer = _submit(source, [{"folder": folder, "entry": f"{AGENT[0]}/ext/other.py"}])
        assert said.startswith("Recorded 0; 1 refused."), (folder, said)
        assert answer.pairs == ()


def test_the_tools_work_when_finished_is_none(tmp_path):
    source = _repo(tmp_path)
    tools = {
        spec.name: spec.handler
        for spec in scan_ai.rooted_file_tools(source, finished=None).tools
    }

    result = _call(tools["read"], {"path": "main.py"})

    assert not result.get("is_error"), _said(result)
    assert "hello" in _said(result)
    assert not _call(tools["glob"], {"pattern": "*.py"}).get("is_error")


def test_two_submits_at_once_keep_exactly_one_set(tmp_path, monkeypatch):
    source = _repo(tmp_path)

    async def checked_after_a_yield(work, *args):
        # A thread's answer comes back on a later turn of the loop; yielding once here makes
        # the two calls interleave the same way on every run.
        await asyncio.sleep(0)
        return work(*args)

    monkeypatch.setattr(scan_ai, "_in_thread", checked_after_a_yield)
    answer = scan_ai._Answer()
    handler = scan_ai._submit_server(source, answer).tools[0].handler
    first = [{"folder": AGENT[0], "entry": AGENT[1]}]
    second = [{"folder": "", "entry": "main.py"}]

    async def both() -> list[dict[str, Any]]:
        return list(await asyncio.gather(handler({"agents": first}), handler({"agents": second})))

    said = [_said(result) for result in asyncio.run(asyncio.wait_for(both(), TEST_BOUND))]

    recorded = "Recorded 1; 0 refused. Your work is done: end your turn now."
    assert sorted(said) == sorted([recorded, scan_ai.SUBMIT_AFTER_SUBMIT])
    assert answer.pairs == ((AGENT,), (("", "main.py"),))[said.index(recorded)]


# -- a path the platform can't carry (the vercel/ai-chatbot run, 6 Nov) -----------------------

ROUTE = "app/(chat)/api/chat/route.ts"


def _route_repo(tmp_path: Path) -> Path:
    source = _repo(tmp_path)
    _write(source / ROUTE, "export async function POST() {}\n")
    return source


def test_a_real_file_the_path_rule_refuses_is_counted_once_and_named(
    tmp_path, monkeypatch, quick
):
    backend = _Backend(
        ("call", SUBMIT, {"agents": [{"folder": "", "entry": ROUTE}]}),
        ("call", SUBMIT, {"agents": [{"folder": "app", "entry": "./" + ROUTE}]}),
        FINISHED,
    )

    outcome = _scan(monkeypatch, _route_repo(tmp_path), backend)

    said = [_said(result) for result in backend.results]
    for line in said:
        assert line.startswith("Recorded 0; 1 refused. 1 name a file whose path the platform")
        assert "don't submit them again" in line
    assert outcome.kind == "empty"
    assert outcome.uncarried == 1


@pytest.mark.parametrize(
    "entry",
    ["app/(chat)/missing.ts", "../outside/(x).ts", "/etc/(x).ts", "node_modules/(x)/a.ts"],
)
def test_a_missing_or_outside_file_is_not_counted_as_uncarried(
    tmp_path, monkeypatch, quick, entry
):
    source = _route_repo(tmp_path)
    _write(source / "node_modules/(x)/a.ts", "x\n")
    backend = _Backend(("call", SUBMIT, {"agents": [{"folder": "", "entry": entry}]}), FINISHED)

    outcome = _scan(monkeypatch, source, backend)

    assert "can't carry" not in _said(backend.results[0])
    assert outcome.kind == "empty"
    assert outcome.uncarried == 0


def test_an_uncarried_file_before_a_kept_submit_is_still_counted(tmp_path, monkeypatch, quick):
    backend = _Backend(
        ("call", SUBMIT, {"agents": [{"folder": "", "entry": ROUTE}]}),
        KEPT,
        FINISHED,
    )

    outcome = _scan(monkeypatch, _route_repo(tmp_path), backend)

    assert outcome.kind == "pairs"
    assert outcome.pairs == (AGENT,)
    assert outcome.refused == 0
    assert outcome.uncarried == 1


def test_glob_reads_a_pattern_as_a_shell_would(tmp_path):
    source = _route_repo(tmp_path)
    _write(source / "package.json", "{}\n")
    _write(source / "web/package.json", "{}\n")
    _write(source / "web/src/page.tsx", "x\n")
    tools = _tools(source)

    def globbed(pattern: str, **extra: str) -> set[str]:
        return _listed(_said(_call(tools["glob"], {"pattern": pattern, **extra})))

    assert globbed("**/package.json") == {"package.json", "web/package.json"}
    assert globbed("**/*.{ts,tsx}") == {ROUTE, "web/src/page.tsx"}
    assert globbed("web/**/page.tsx") == {"web/src/page.tsx"}
    assert globbed("*.{py,json}") >= {"main.py", "package.json", AGENT[1]}
    assert _said(_call(tools["glob"], {"pattern": "{"})) == "No file matches."
    assert len(scan_ai._pattern_forms("{a,b}" * 20)) <= scan_ai._MAX_FORMS
    found = _said(_call(tools["grep"], {"text": "POST", "glob": "**/*.{ts,tsx}"}))
    assert _listed(found) == {ROUTE}


def test_a_failed_cost_note_keeps_the_answer_and_logs_its_trace(tmp_path, monkeypatch, quick, caplog):
    """A paid answer is never turned into a retry by the cost note, and the note's fault is
    logged with its trace so it can be found."""
    backend = _Backend(KEPT, FINISHED)
    monkeypatch.setattr(session, "resolve", lambda *_args, **_kwargs: backend)

    def broken_note() -> None:
        raise OSError("cost file not writable")

    with caplog.at_level(logging.WARNING, logger=scan_ai.logger.name):
        outcome = asyncio.run(
            asyncio.wait_for(
                scan_ai.run_ai_step(
                    _repo(tmp_path), hints="x", seconds=5.0, on_turn_end=broken_note
                ),
                TEST_BOUND,
            )
        )

    assert outcome.kind == "pairs"
    assert outcome.pairs == (AGENT,)
    assert "the cost note after the turn failed" in caplog.text
    assert "Traceback" in caplog.text and "OSError" in caplog.text
