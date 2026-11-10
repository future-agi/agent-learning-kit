"""What a Claude stage records: each turn's own tokens, priced here, never the CLI's estimate.

The Claude Code CLI reports running totals within one connection, so a stage that sends a second
message would count its first turn again if results were added as reported. The simulated
customer's tokens are billed from these figures, so they must be each query's own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import ResultMessage

from fi.alk.harness import spend
from fi.alk.harness.backends import ConversationSession, SessionSpec
from fi.alk.harness.backends import vertex_gemini
from fi.alk.harness.backends.claude import ClaudeBackend, ClaudeSession
from fi.alk.harness.backends.vertex_gemini import priced
from fi.alk.harness.session import Stage, Turn

M = "gemini-3.7-flash"


@pytest.fixture(autouse=True)
def _a_priced_day(monkeypatch):
    """Prices are a dated schedule; these tests are about counting, so the day is fixed."""

    class _Today(date):
        @classmethod
        def today(cls):
            return date(2026, 12, 1)

    monkeypatch.setattr(vertex_gemini, "date", _Today)
    # A journal the machine names must not receive these tests' turns.
    monkeypatch.delenv(spend.JOURNAL_ALIAS, raising=False)
    spend._stages.clear()
    spend.journal_to(None)
    yield
    spend._stages.clear()
    spend.journal_to(None)


def _usage(tokens_in, tokens_out=0, cached=0):
    return {
        "inputTokens": tokens_in,
        "outputTokens": tokens_out,
        "cacheReadInputTokens": cached,
    }


def _result(model_usage, *, usage=None, cli_cost=9.99):
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        total_cost_usd=cli_cost,
        usage=usage,
        model_usage=model_usage,
    )


def _session(*, resume=None, reported=None):
    return ClaudeSession(SimpleNamespace(resume=resume), reported_model=reported)


def _done(session, message):
    [done] = session._translate(message)
    return done


class _NoProcess:
    """The SDK client, minus the CLI: only the session's own bookkeeping is under test."""

    def __init__(self, options):
        pass

    async def connect(self):
        pass

    async def disconnect(self):
        pass


def test_two_results_on_one_connection_record_their_own_turns():
    session = _session()
    first = _done(session, _result({M: _usage(100_000, 1_000)}))
    second = _done(session, _result({M: _usage(200_000, 2_000)}))

    assert (first.tokens_in, first.tokens_out) == (100_000, 1_000)
    assert (second.tokens_in, second.tokens_out) == (100_000, 1_000)
    assert first.cost_usd is not None
    assert second.cost_usd == pytest.approx(priced(M, 100_000, 1_000))


def test_a_new_connection_takes_its_first_result_whole(monkeypatch):
    """Larger than the old baseline on purpose: a kept baseline would give 50k, not fail."""
    monkeypatch.setattr("fi.alk.harness.backends.claude.ClaudeSDKClient", _NoProcess)
    session = _session()

    async def reconnect():
        await session.start()
        _done(session, _result({M: _usage(100_000, 1_000)}))
        await session.stop()
        await session.start()
        return _done(session, _result({M: _usage(150_000, 1_500)}))

    again = asyncio.run(reconnect())

    assert (again.tokens_in, again.tokens_out) == (150_000, 1_500)


def test_cache_reads_are_priced_at_the_cache_rate():
    """Cached input costs a tenth of fresh input on Vertex; pricing it as fresh overstates the turn."""
    done = _done(_session(), _result({M: _usage(100_000, 0, cached=900_000)}))

    assert (done.tokens_in, done.tokens_cached) == (1_000_000, 900_000)
    assert done.cost_usd == pytest.approx(priced(M, 1_000_000, 0, 900_000))
    assert done.cost_usd == pytest.approx(0.1425)


def test_a_turn_with_input_and_no_output_is_still_a_report():
    """Only a result with no counts at all is "no report"; input alone was still spent."""
    done = _done(_session(), _result({M: _usage(1_000, 0)}))

    assert (done.tokens_in, done.tokens_out) == (1_000, 0)
    assert done.cost_usd == pytest.approx(priced(M, 1_000, 0))
    assert done.cost_usd > 0


def test_a_lower_count_takes_all_three_counts_whole():
    """Totals that went down restarted where the kit couldn't see; none of them is a difference."""
    session = _session()
    _done(session, _result({M: _usage(100_000, 1_000, cached=50_000)}))
    after = _done(session, _result({M: _usage(120_000, 500, cached=60_000)}))
    following = _done(session, _result({M: _usage(130_000, 600, cached=70_000)}))

    assert (after.tokens_in, after.tokens_out, after.tokens_cached) == (
        180_000,
        500,
        60_000,
    )
    # The restarted totals are the new baseline, so the next result is only its own turn.
    assert (following.tokens_in, following.tokens_out, following.tokens_cached) == (
        20_000,
        100,
        10_000,
    )


def test_fewer_fresh_input_tokens_take_all_three_counts_whole():
    """Fresh input never falls within one connection, so a fall is an unseen restart. A difference
    there would record more cached than input tokens, which the platform's authoring bill refuses."""
    session = _session()
    _done(session, _result({M: _usage(100_000, 1_000, cached=10_000)}))
    after = _done(session, _result({M: _usage(5_000, 1_100, cached=200_000)}))

    assert (after.tokens_in, after.tokens_out, after.tokens_cached) == (
        205_000,
        1_100,
        200_000,
    )
    assert after.tokens_cached <= after.tokens_in


def test_a_resumed_result_with_no_usage_says_so(caplog):
    """Its carried totals can't be told apart from the old process's, so it records 0, aloud."""
    caplog.set_level(logging.WARNING, logger="fi.alk.harness.backends.claude")
    done = _done(
        _session(resume="earlier"), _result({M: _usage(500_000, 5_000)}, usage=None)
    )

    assert (done.tokens_in, done.tokens_out, done.tokens_cached) == (0, 0, 0)
    assert "reported no usage" in caplog.text


def test_an_unpriced_model_is_unpriced_even_with_the_cli_estimate():
    """The CLI's estimate for a non-Claude model is a guess, about 7x Gemini's rate."""
    session = _session()
    done = _done(
        session, _result({"a-model-nobody-priced": _usage(1_000, 10)}, cli_cost=0.5)
    )

    assert done.cost_usd is None
    Stage(SessionSpec(system_prompt="x"), name="customer")._events(done, Turn())
    ledger = spend.snapshot()
    assert ledger["unpriced_turns"] == 1
    assert ledger["total_usd"] == 0.0


def test_an_alias_on_the_wire_is_priced_by_the_reported_model():
    session = _session(reported=M)
    done = _done(session, _result({"alias-on-the-wire": _usage(1_000_000, 10)}))

    assert done.cost_usd == pytest.approx(priced(M, 1_000_000, 10))
    assert done.models == {M}


@pytest.mark.parametrize(
    "nothing", [{}, None, {M: _usage(0)}], ids=["empty", "missing", "all-zero"]
)
def test_a_result_with_no_usage_costs_nothing_and_keeps_the_baseline(nothing):
    """A first-call error arrives with no usage. It cost nothing, and None would mark a reading
    incomplete; resetting the baseline would count every earlier turn again."""
    session = _session()
    _done(session, _result({M: _usage(100_000, 1_000)}))
    empty = _done(session, _result(nothing, cli_cost=0.0))
    after = _done(session, _result({M: _usage(130_000, 1_300)}))

    assert (empty.tokens_in, empty.tokens_out, empty.tokens_cached) == (0, 0, 0)
    assert empty.cost_usd == 0.0
    assert (after.tokens_in, after.tokens_out) == (30_000, 300)


def test_a_resumed_first_result_takes_usage_not_the_carried_totals():
    """SDK 0.2.176 on carries the old process's totals over a resume; `usage` is this query's."""
    session = _session(resume="earlier")
    first = _done(
        session,
        _result(
            {M: _usage(500_000, 5_000)},
            usage={
                "input_tokens": 30_000,
                "cache_read_input_tokens": 10_000,
                "output_tokens": 70,
            },
        ),
    )
    second = _done(session, _result({M: _usage(520_000, 5_007)}))

    assert (first.tokens_in, first.tokens_out, first.tokens_cached) == (
        40_000,
        70,
        10_000,
    )
    assert (second.tokens_in, second.tokens_out) == (20_000, 7)


def test_two_priced_models_with_no_alias_give_the_higher_price():
    session = _session()
    done = _done(
        session,
        _result(
            {M: _usage(600_000, 6_000), "gemini-3.5-flash": _usage(400_000, 4_000)}
        ),
    )

    cheaper, dearer = (
        priced(M, 1_000_000, 10_000),
        priced("gemini-3.5-flash", 1_000_000, 10_000),
    )
    assert dearer > cheaper
    assert done.cost_usd == pytest.approx(dearer)


# -- the bundled CLI against a local stub, through ClaudeBackend, ClaudeSession and Stage ---------


class _MessagesStub(BaseHTTPRequestHandler):
    """The Messages API on 127.0.0.1. Every request gets its own usage, and is logged as the truth
    the ledger must equal."""

    served = 0
    log: list[tuple[int, int]] = []

    def log_message(self, *args) -> None:
        pass

    def _json(self, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _event(self, name: str, data: dict) -> None:
        self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.end_headers()

    def do_GET(self) -> None:
        self._json({"data": [], "has_more": False})

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            request = json.loads(raw)
        except ValueError:
            request = {}
        if "count_tokens" in self.path:
            self._json({"input_tokens": 10})
            return
        model = request.get("model") or M
        _MessagesStub.served += 1
        n = _MessagesStub.served
        fresh, cached, written = 1_000 * n, 100 * n, 10 * n
        _MessagesStub.log.append((fresh + cached, written))
        usage = {
            "input_tokens": fresh,
            "output_tokens": written,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": cached,
        }
        if not request.get("stream"):
            self._json(
                {
                    "id": f"msg_{n}",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": usage,
                }
            )
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self._event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_{n}",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {**usage, "output_tokens": 1},
                },
            },
        )
        self._event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )
        self._event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Sure, go on."},
            },
        )
        self._event("content_block_stop", {"type": "content_block_stop", "index": 0})
        self._event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": written},
            },
        )
        self._event("message_stop", {"type": "message_stop"})


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """The real CLI, aimed at the stub. The key is a placeholder, and every other route it could
    take points at a closed local port."""
    _MessagesStub.served = 0
    _MessagesStub.log = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MessagesStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    nowhere = "http://127.0.0.1:9"
    for name, value in {
        "ALK_CLAUDE_GATEWAY_URL": f"http://127.0.0.1:{server.server_address[1]}",
        "ALK_CLAUDE_GATEWAY_API_KEY": "placeholder-not-a-key",
        "ANTHROPIC_VERTEX_BASE_URL": nowhere,
        "ANTHROPIC_BEDROCK_BASE_URL": nowhere,
        "ANTHROPIC_FOUNDRY_BASE_URL": nowhere,
        "OTEL_EXPORTER_OTLP_ENDPOINT": nowhere,
        "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
    }.items():
        monkeypatch.setenv(name, value)
    for name in (
        "AGENTCC_BASE_URL",
        "AGENTCC_API_KEY",
        "AGENTCC_CLAUDE_MODEL_ALIAS",
        "ALK_HARNESS",
        "ALK_HARNESS_MODEL",
        spend.JOURNAL_ALIAS,
    ):
        monkeypatch.delenv(name, raising=False)
    # No alias on the wire, so the ledger's model is the one the table prices.
    monkeypatch.setattr("fi.agentcc.gateway", lambda: None, raising=False)
    work = tmp_path / "work"
    work.mkdir()
    # The customer's stage names no folder, so the CLI runs where the test does.
    monkeypatch.chdir(work)
    try:
        yield work
    finally:
        server.shutdown()
        server.server_close()


def _truth() -> tuple[int, int]:
    return (
        sum(tokens_in for tokens_in, _ in _MessagesStub.log),
        sum(tokens_out for _, tokens_out in _MessagesStub.log),
    )


def _spec(work, conversation=None) -> SessionSpec:
    return SessionSpec(
        system_prompt="Be brief.",
        max_turns=3,
        model=M,
        gated=False,
        cwd=str(work),
        conversation=conversation,
    )


def _ledger(stage: str) -> dict:
    [entry] = [one for one in spend.snapshot()["stages"] if one["stage"] == stage]
    return entry


def test_the_cli_two_messages_on_one_connection_record_each_turn(cli):
    async def talk():
        async with Stage(_spec(cli), name="author", backend=ClaudeBackend()) as stage:
            await stage.say("one")
            await stage.say("two")

    asyncio.run(asyncio.wait_for(talk(), 150))

    entry = _ledger("author")
    assert len(_MessagesStub.log) >= 2
    assert (entry["tokens_in"], entry["tokens_out"]) == _truth()
    assert entry["unpriced"] == 0


def test_the_cli_a_resume_then_one_more_message_is_the_true_sum(cli):
    """Newer SDKs carry the totals over a resume, older ones restart them; either way the ledger
    is what the queries used."""

    def conversation(resume=None):
        return ConversationSession(
            app_name="alk",
            user_id="u",
            session_id="conversation",
            resume_session_id=resume,
        )

    async def talk():
        async with Stage(
            _spec(cli, conversation()), name="chat", backend=ClaudeBackend()
        ) as stage:
            await stage.say("one")
            await stage.say("two")
        assert stage.session_id
        async with Stage(
            _spec(cli, conversation(stage.session_id)),
            name="chat",
            backend=ClaudeBackend(),
        ) as again:
            await again.say("three")

    asyncio.run(asyncio.wait_for(talk(), 150))

    entry = _ledger("chat")
    assert len(_MessagesStub.log) >= 3
    assert (entry["tokens_in"], entry["tokens_out"]) == _truth()


def test_the_cli_customer_lines_bill_their_true_tokens(cli):
    """The simulated customer is billed from `simulator_input_tokens`, one query per line."""
    from fi.alk.harness.contract import AgentContract
    from fi.alk.harness.run import conversation
    from fi.alk.harness.scenario import Scenario

    async def agent_replies(text):
        return "Your order ships tomorrow."

    async def nothing(*_args):
        return None

    target = SimpleNamespace(
        open=nothing, say=agent_replies, close=nothing, spent_usd=0.0
    )
    # The opening and two answers: three customer lines on one connection.
    scenario = Scenario(
        name="order", instruction="Ask where your order is.", max_turns=2
    )

    transcript = asyncio.run(
        asyncio.wait_for(
            conversation.converse(target, scenario, AgentContract(), model=M), 150
        )
    )

    assert [one.speaker for one in transcript.exchanges].count("customer") == 3
    assert len(_MessagesStub.log) >= 3
    assert (
        transcript.simulator_input_tokens,
        transcript.simulator_output_tokens,
    ) == _truth()


# -- #225's tripwire ------------------------------------------------------------------------------


def test_a_claude_reading_adds_per_turn_figures_as_they_come(tmp_path, monkeypatch):
    """Skipped until #225's reading lands. It subtracts Claude's running totals itself, which on
    per-turn figures would record the first turn and nothing after it."""
    agent_reading = pytest.importorskip("fi.alk.harness.agent_reading")

    from fi.alk.harness import reading_entry, session
    from fi.alk.harness.backends import StageDone

    class _LoudExit:
        """A reading's hard limit ending this process is a failure, not a silent exit."""

        def __getattr__(self, name):
            return getattr(os, name)

        @staticmethod
        def _exit(_code):
            os.write(2, b"a reading's hard limit ended the test process\n")
            os._exit(97)

    class _Live:
        async def start(self):
            pass

        async def stop(self):
            pass

        async def send(self, message):
            pass

        async def interrupt(self):
            return True

        async def replies(self):
            # What this backend now reports: each turn's own cost, not the running total.
            yield StageDone(
                outcome="success", turns=1, cost_usd=0.07875, tokens_in=100_000
            )

    class _Claude:
        name = "claude"
        default_model = vertex_gemini.DEFAULT_MODEL

        def create(self, spec):
            return _Live()

        def can_drive(self, model):
            return True

    monkeypatch.setattr(reading_entry, "os", _LoudExit())
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (tmp_path / "no-secrets.json",))
    monkeypatch.setenv("ALK_HARNESS_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("ALK_READING_MAX_DURATION_SECONDS", "20")
    monkeypatch.setattr(session, "resolve", lambda *a, **k: _Claude())
    source = tmp_path / "source" / "agents" / "support"
    source.mkdir(parents=True)
    (source / "agent.py").write_text("x = 1\n", encoding="utf-8")
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "job_id": "job",
                "run_id": "run",
                "execution": "hosted",
                "source": {
                    "kind": "github",
                    "visibility": "public",
                    "repository": "acme/agent",
                    "commit_sha": "a" * 40,
                },
                "runtime": {"isolation": "dedicated_vm"},
                "agent": {"connector": "auto"},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "reading"

    # The opening and the reminder to submit: two turns, each with its own figures.
    reading_entry.run_reading_mode(
        agent_reading._read_agent(interrupt_wait=1.0),
        job_path=job,
        source=tmp_path / "source",
        output=output,
    )

    cost = json.loads((output / "cost.json").read_text(encoding="utf-8"))
    assert cost["total_usd"] == pytest.approx(0.1575)


def test_ci_runs_these_tests():
    """The check-by date is enforced by CI going red, which only happens if CI runs it."""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sdk-smoke.yml"
    )
    # A whole line of the pytest list, so a commented-out or renamed entry doesn't count.
    listed = {
        line.strip() for line in workflow.read_text(encoding="utf-8").splitlines()
    }
    assert "tests/harness/test_spend.py" in listed
    assert "tests/harness/test_claude_cost.py" in listed
