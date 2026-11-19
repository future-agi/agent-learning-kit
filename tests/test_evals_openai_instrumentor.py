"""The evals OpenAI instrumentor against the installed openai, through a local stub of the API.

It patches `openai.resources` classes by name, so this runs on whatever openai CI installs: the
clean-install job gets openai 3, and the minimum-LiveKit step openai 2.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

openai = pytest.importorskip("openai")
pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from fi.evals.otel.instrumentors.openai import OpenAIInstrumentor  # noqa: E402

COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "stub-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hello"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
}


class _Stub(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps(COMPLETION).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


@pytest.fixture
def stub_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


def test_chat_calls_are_traced_and_uninstrument_restores_the_client(stub_url) -> None:
    from openai.resources.chat import completions

    original_sync = completions.Completions.create
    original_async = completions.AsyncCompletions.create
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentor = OpenAIInstrumentor()
    instrumentor._tracer = provider.get_tracer("test")
    instrumentor.instrument()
    try:
        assert instrumentor.is_instrumented
        assert completions.Completions.create is not original_sync
        messages = [{"role": "user", "content": "hi"}]
        client = openai.OpenAI(api_key="placeholder-not-a-key", base_url=stub_url)
        reply = client.chat.completions.create(model="stub-model", messages=messages)
        assert reply.choices[0].message.content == "hello"

        async def ask_async():
            async with openai.AsyncOpenAI(
                api_key="placeholder-not-a-key", base_url=stub_url
            ) as async_client:
                return await async_client.chat.completions.create(
                    model="stub-model", messages=messages
                )

        assert asyncio.run(ask_async()).choices[0].message.content == "hello"
    finally:
        instrumentor.uninstrument()

    assert completions.Completions.create is original_sync
    assert completions.AsyncCompletions.create is original_async
    spans = exporter.get_finished_spans()
    assert len(spans) == 2, [span.name for span in spans]
    assert all(
        span.attributes.get("gen_ai.request.model") == "stub-model" for span in spans
    )
