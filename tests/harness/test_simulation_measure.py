"""Measuring an untraced agent's model calls, in the agent's own interpreter.

Each case runs a real agent process whose world directory holds the hook as the sandbox writes it.
The agent's model SDK talks to a mock transport, so nothing leaves the machine, and the spans go
to a stand-in collector in this process. What is checked is what leaves the agent: the SDK
instrumentors' spans and nothing else, with the call id, the model and tokens, and no prompt,
reply or error text; a model call nothing measured marks its call partial.

The cases need the instrumentors the build installs beside an agent's SDKs, which ALK itself does
not depend on. Run them with::

    uv run --with traceai-openai==0.1.15 --with traceai-anthropic==0.1.14 --with anthropic \
        --with langchain-openai --with traceai-langchain==0.2.1 --with langfuse \
        pytest tests/harness/test_simulation_measure.py
"""

from __future__ import annotations

import gzip
import importlib
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fi.alk.harness import process_runtime, simulation_measure

pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # noqa: E402
    ExportTraceServiceRequest,
)

CALL = "call-7f3a"
PROMPT = "PROMPT-TEXT-1c9e"
SYSTEM = "SYSTEM-TEXT-5b2d"
REPLY = "REPLY-TEXT-8e41"
INGEST = ("ingest-api-key", "ingest-secret-key")
STATE = simulation_measure.STATE_KEY
HOOK_MODULES = (
    "simulation_mark_bootstrap.py",
    "simulation_worker_events.py",
    "simulation_measure.py",
)

OPENAI_CALL = f"""
import httpx, openai

def answer(request):
    return httpx.Response(200, json={{
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
        "model": "gpt-4o-mini-2024-07-18",
        "choices": [{{"index": 0, "finish_reason": "stop",
                     "message": {{"role": "assistant", "content": "{REPLY}"}}}}],
        "usage": {{"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}},
    }})

client = openai.OpenAI(api_key="sk-test", http_client=httpx.Client(transport=httpx.MockTransport(answer)))

def ask():
    out = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{{"role": "system", "content": "{SYSTEM}"}}, {{"role": "user", "content": "{PROMPT}"}}],
    )
    return out.choices[0].message.content
"""

ANTHROPIC_CALL = f"""
import httpx, anthropic

def answer(request):
    return httpx.Response(200, json={{
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude-3-5-haiku-20241022",
        "content": [{{"type": "text", "text": "{REPLY}"}}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {{"input_tokens": 11, "output_tokens": 7}},
    }})

client = anthropic.Anthropic(api_key="sk-test", http_client=httpx.Client(transport=httpx.MockTransport(answer)))

def ask():
    out = client.messages.create(
        model="claude-3-5-haiku-latest", max_tokens=50, system="{SYSTEM}",
        messages=[{{"role": "user", "content": "{PROMPT}"}}],
    )
    return out.content[0].text
"""

RAW_MODEL_REQUEST = """
import httpx

def raw_answer(request):
    return httpx.Response(200, json={"ok": True})

httpx.Client(transport=httpx.MockTransport(raw_answer)).post(
    "https://api.anthropic.com/v1/messages", json={"messages": []}
)
"""


class Collector:
    """An OTLP/HTTP endpoint that keeps every export it is sent."""

    def __init__(self) -> None:
        self.received: list[tuple[str, dict[str, str], bytes]] = []
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - the http.server hook
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                collector.received.append(
                    (self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1/traces"

    def spans(self) -> list[dict]:
        found = []
        for _path, _headers, body in self.received:
            request = ExportTraceServiceRequest.FromString(body)
            for resource_spans in request.resource_spans:
                resource = _attributes(resource_spans.resource.attributes)
                for scope_spans in resource_spans.scope_spans:
                    for span in scope_spans.spans:
                        found.append(
                            {
                                "name": span.name,
                                "scope": scope_spans.scope.name,
                                "attributes": _attributes(span.attributes),
                                "resource": resource,
                            }
                        )
        return found

    def raw(self) -> bytes:
        return b"".join(body for _path, _headers, body in self.received)

    def close(self) -> None:
        self.server.shutdown()


def _attributes(pairs) -> dict:
    out = {}
    for pair in pairs:
        kind = pair.value.WhichOneof("value")
        out[pair.key] = getattr(pair.value, kind) if kind else None
    return out


@pytest.fixture
def collector():
    serving = Collector()
    yield serving
    serving.close()


def _world(root: Path) -> Path:
    """The world directory the sandbox writes for an exporting process."""
    world = root / "world"
    world.mkdir()
    (world / "sitecustomize.py").write_text(
        process_runtime._SIMULATION_MARK_SITECUSTOMIZE, encoding="utf-8"
    )
    source = Path(process_runtime.__file__).parent
    for name in HOOK_MODULES:
        (world / name).write_bytes((source / name).read_bytes())
    return world


def _environment(
    root: Path,
    collector: Collector,
    *,
    measure: bool = True,
    extra_path: tuple[Path, ...] = (),
    **overrides: str,
) -> dict[str, str]:
    """The environment the sandbox gives the agent when the platform asks for measuring."""
    call_file = root / "call"
    call_file.write_text(CALL, encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OTEL_", "FI_", "LANGFUSE_", "ALK_", "HARNESS_"))
    }
    env.update(
        PYTHONPATH=os.pathsep.join(
            [str(_world(root)), *(str(path) for path in extra_path)]
        ),
        OTEL_RESOURCE_ATTRIBUTES=(
            "project_name=hidden-simulation,fi.simulation.origin=simulation,"
            "fi.simulation.job_id=job-1"
        ),
        FI_SIMULATION_EXPORT="our_keys",
        FI_SIMULATION_CALL_FILE=str(call_file),
        FI_SIMULATION_INGEST_API_KEY=INGEST[0],
        FI_SIMULATION_INGEST_SECRET_KEY=INGEST[1],
    )
    if measure:
        env.update(
            FI_SIMULATION_INSTRUMENT="traceai",
            FI_SIMULATION_TRACES_ENDPOINT=collector.url,
        )
    env.update(overrides)
    return env


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _settled(collector: Collector, count: int, seconds: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and len(collector.spans()) < count:
        time.sleep(0.05)
    return collector.spans()


def _measured_scope(sdk: str) -> str:
    return simulation_measure.SDK_INSTRUMENTORS[sdk][0]


def _needs(*modules: str) -> None:
    for module in modules:
        pytest.importorskip(module)


@pytest.mark.parametrize(
    "sdk, call, model",
    [
        ("openai", OPENAI_CALL, "gpt-4o-mini"),
        ("anthropic", ANTHROPIC_CALL, "claude-3-5-haiku"),
    ],
)
def test_an_untraced_agents_model_call_is_measured_without_its_text(
    tmp_path: Path, collector: Collector, sdk: str, call: str, model: str
) -> None:
    _needs(sdk, _measured_scope(sdk))
    result = _run(call + "print(ask())", _environment(tmp_path, collector))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == REPLY
    [span] = _settled(collector, 1)
    assert span["scope"] == _measured_scope(sdk)
    attributes = span["attributes"]
    assert attributes["fi.simulator.call_execution_id"] == CALL
    assert model in str(
        attributes.get("gen_ai.request.model") or attributes.get("llm.model_name")
    )
    assert attributes["gen_ai.usage.input_tokens"] == 11
    assert attributes["gen_ai.usage.output_tokens"] == 7
    assert STATE not in attributes
    assert span["resource"]["project_name"] == "hidden-simulation"
    assert span["resource"]["fi.simulation.job_id"] == "job-1"
    # No prompt, system prompt or reply leaves the process, in any span or attribute.
    for text in (PROMPT, SYSTEM, REPLY):
        assert text.encode() not in collector.raw()
    # Sent under the agent's ingest key.
    _path, headers, _body = collector.received[0]
    assert (headers["x-api-key"], headers["x-secret-key"]) == INGEST


def test_nothing_is_measured_or_imported_without_the_switch(
    tmp_path: Path, collector: Collector
) -> None:
    _needs("openai")
    result = _run(
        OPENAI_CALL + "ask()\nimport sys\nprint('opentelemetry' in sys.modules)",
        _environment(tmp_path, collector, measure=False),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
    assert collector.received == []


def test_a_call_through_a_framework_and_its_sdk_is_counted_once(
    tmp_path: Path, collector: Collector
) -> None:
    """LangChain's own model span, from the framework instrumentor the sandbox also turns on,
    stays in the process: only the SDK's span is billed."""
    _needs("langchain_openai", "traceai_langchain", "traceai_openai")
    tool_trace = tmp_path / "agent-tool-calls.jsonl"
    script = (
        OPENAI_CALL
        + """
from langchain_openai import ChatOpenAI
chat = ChatOpenAI(model="gpt-4o-mini", api_key="sk-test", http_client=client._client)
print(chat.invoke([("system", "%s"), ("user", "%s")]).content)
"""
        % (SYSTEM, PROMPT)
    )
    result = _run(
        script,
        _environment(
            tmp_path,
            collector,
            ALK_OBSERVE_FRAMEWORKS="1",
            HARNESS_TOOL_TRACE=str(tool_trace),
        ),
    )

    assert result.returncode == 0, result.stderr
    # The process flushed at exit, so every span it sent is here.
    spans = collector.spans()
    assert [span["scope"] for span in spans] == ["traceai_openai"]
    assert spans[0]["attributes"]["fi.simulator.call_execution_id"] == CALL


def test_an_agent_that_instruments_the_sdk_itself_keeps_its_own_tracing(
    tmp_path: Path, collector: Collector
) -> None:
    _needs("traceai_openai")
    script = (
        OPENAI_CALL
        + """
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from traceai_openai import OpenAIInstrumentor
own = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(own))
OpenAIInstrumentor().instrument(tracer_provider=provider)
ask()
print(len(own.get_finished_spans()))
"""
    )
    result = _run(script, _environment(tmp_path, collector))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
    time.sleep(0.5)
    assert collector.spans() == []


def test_an_agent_with_its_own_tracer_provider_keeps_it_and_ours_stays_out(
    tmp_path: Path, collector: Collector
) -> None:
    """An agent that traces through its own provider, as a gRPC or Langfuse setup does, still
    reads as untraced to authoring. Its exporter, which reads the standard OTLP variable, keeps
    sending where it did, and neither provider receives the other's spans."""
    _needs("traceai_openai")
    own = Collector()
    try:
        script = (
            OPENAI_CALL
            + """
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
with trace.get_tracer("agent").start_as_current_span("agent turn"):
    ask()
provider.shutdown()
"""
        )
        result = _run(
            script,
            _environment(
                tmp_path,
                collector,
                OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=own.url,
            ),
        )

        assert result.returncode == 0, result.stderr
        assert [span["name"] for span in _settled(own, 1)] == ["agent turn"]
        assert [span["scope"] for span in _settled(collector, 1)] == ["traceai_openai"]
        # The agent's backend never receives the sandbox's ingest key.
        for _path, headers, _body in own.received:
            assert headers.get("x-api-key") != INGEST[0]
            assert headers.get("x-secret-key") != INGEST[1]
    finally:
        own.close()


def test_a_langfuse_agent_keeps_its_own_traces(
    tmp_path: Path, collector: Collector
) -> None:
    _needs("langfuse", "traceai_openai")
    langfuse = Collector()
    try:
        script = (
            OPENAI_CALL.replace(
                "import httpx, openai",
                "import httpx\nfrom langfuse.openai import openai",
            )
            + "ask()\nfrom langfuse import get_client\nget_client().flush()\n"
        )
        host = langfuse.url.rsplit("/v1/traces", 1)[0]
        result = _run(
            script,
            _environment(
                tmp_path,
                collector,
                LANGFUSE_HOST=host,
                LANGFUSE_PUBLIC_KEY="pk-lf-test",
                LANGFUSE_SECRET_KEY="sk-lf-test",
            ),
        )

        assert result.returncode == 0, result.stderr
        assert _settled(langfuse, 1), "the agent's own Langfuse traces stopped"
        assert all(span["scope"] != "traceai_openai" for span in langfuse.spans())
        assert [span["scope"] for span in _settled(collector, 1)] == ["traceai_openai"]
    finally:
        langfuse.close()


@pytest.mark.parametrize("delay", [0.0, 0.2])
def test_a_sigterm_right_after_a_reply_keeps_its_span(
    tmp_path: Path, collector: Collector, delay: float
) -> None:
    _needs("traceai_openai")
    agent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(OPENAI_CALL)
            + "print(ask(), flush=True)\nimport time\ntime.sleep(60)\n",
        ],
        env=_environment(tmp_path, collector),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert agent.stdout.readline().strip() == REPLY
        time.sleep(delay)
        agent.send_signal(signal.SIGTERM)
        assert agent.wait(timeout=10) == -signal.SIGTERM
    finally:
        if agent.poll() is None:
            agent.kill()
    assert [span["scope"] for span in collector.spans()] == ["traceai_openai"]


def test_a_raw_model_request_marks_its_call_partial(
    tmp_path: Path, collector: Collector
) -> None:
    _needs("traceai_openai")
    result = _run(
        OPENAI_CALL + "ask()\n" + RAW_MODEL_REQUEST + "ask()\n",
        _environment(tmp_path, collector),
    )

    assert result.returncode == 0, result.stderr
    spans = _settled(collector, 3)
    measured = [span for span in spans if span["scope"] == "traceai_openai"]
    markers = [span for span in spans if span["scope"] == simulation_measure.SCOPE]
    assert len(measured) == 2 and len(markers) == 1
    [marker] = markers
    assert marker["attributes"][STATE] == "partial"
    assert marker["attributes"]["fi.simulator.call_execution_id"] == CALL
    # The SDK's own requests run inside its spans and mark nothing; the call after the raw
    # request is stamped.
    assert sorted(span["attributes"].get(STATE, "") for span in measured) == [
        "",
        "partial",
    ]


def test_an_sdk_nothing_here_covers_marks_the_process_partial(
    tmp_path: Path, collector: Collector
) -> None:
    _needs("traceai_openai")
    stand_in = tmp_path / "packages"
    (stand_in / "vertexai").mkdir(parents=True)
    (stand_in / "vertexai" / "__init__.py").write_text("", encoding="utf-8")
    result = _run(
        "import vertexai\n" + OPENAI_CALL + "ask()\n",
        _environment(tmp_path, collector, extra_path=(stand_in,)),
    )

    assert result.returncode == 0, result.stderr
    spans = _settled(collector, 2)
    measured = [span for span in spans if span["scope"] == "traceai_openai"]
    assert [span["attributes"][STATE] for span in measured] == ["partial"]
    assert "unmeasured vertexai" in result.stderr


def test_a_missing_instrumentor_never_stops_the_agent_and_marks_it_partial(
    tmp_path: Path, collector: Collector
) -> None:
    """The build skipped the instrumentor (a pinned version it does not fit, or Python 3.14):
    the agent starts as it would, and its call reads unmeasured, never free."""
    _needs("openai")
    missing = tmp_path / "missing"
    (missing / "traceai_openai").mkdir(parents=True)
    (missing / "traceai_openai" / "__init__.py").write_text(
        "raise ImportError('not installed')\n", encoding="utf-8"
    )
    result = _run(
        OPENAI_CALL + "print(ask())",
        _environment(tmp_path, collector, extra_path=(missing,)),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == REPLY
    assert "unmeasured openai, its instrumentor is not installed" in result.stderr
    [marker] = _settled(collector, 1)
    assert marker["scope"] == simulation_measure.SCOPE
    assert marker["attributes"][STATE] == "partial"
    assert marker["attributes"]["fi.simulator.call_execution_id"] == CALL


@pytest.fixture
def measuring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The module, fresh, as the hook registers it, with a provider that is never made."""
    (tmp_path / "call").write_text(CALL, encoding="utf-8")
    for name, value in {
        "FI_SIMULATION_INSTRUMENT": "traceai",
        "FI_SIMULATION_TRACES_ENDPOINT": "http://127.0.0.1:9/v1/traces",
        "FI_SIMULATION_INGEST_API_KEY": INGEST[0],
        "FI_SIMULATION_INGEST_SECRET_KEY": INGEST[1],
        "FI_SIMULATION_CALL_FILE": str(tmp_path / "call"),
    }.items():
        monkeypatch.setenv(name, value)
    module = importlib.reload(simulation_measure)
    marked: list[str] = []
    monkeypatch.setattr(module, "_mark_partial", lambda: marked.append("partial"))
    monkeypatch.setattr(module, "_flush_on_sigterm", lambda: None)
    yield module, marked
    importlib.reload(simulation_measure)


def test_a_urllib3_request_to_a_model_api_marks_partial(measuring) -> None:
    module, marked = measuring

    class HTTPConnectionPool:
        def __init__(self, host):
            self.host = host

        def urlopen(self, method, url, **_kwargs):
            return "sent"

    fake = types.SimpleNamespace(HTTPConnectionPool=HTTPConnectionPool)
    module._patch_urllib3(fake)

    assert (
        HTTPConnectionPool("collector.internal").urlopen("POST", "/v1/traces") == "sent"
    )
    assert marked == []
    for host in (
        "api.openai.com",
        "my-resource.openai.azure.com",
        "bedrock-runtime.us-east-1.amazonaws.com",
    ):
        assert HTTPConnectionPool(host).urlopen("POST", "/v1") == "sent"
    assert marked == ["partial"] * 3


def test_register_needs_the_switch_and_the_destination(
    measuring, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _marked = measuring
    lines: list[str] = []
    patches: dict = {}

    assert module.register(patches, lines.append) is True
    assert {
        "openai",
        "anthropic",
        "httpx",
        "urllib3.connectionpool",
        "vertexai",
    } <= set(patches)

    monkeypatch.delenv("FI_SIMULATION_TRACES_ENDPOINT")
    patches = {}
    assert module.register(patches, lines.append) is False
    assert patches == {} and lines == ["failed"]

    monkeypatch.setenv("FI_SIMULATION_INSTRUMENT", "other")
    assert module.register({}, lines.append) is False
    assert lines == ["failed"]
