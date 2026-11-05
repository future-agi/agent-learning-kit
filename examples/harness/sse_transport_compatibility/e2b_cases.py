#!/usr/bin/env python3
"""Run the fixed ten-case SSE qualification inside one E2B sandbox."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator


CASE_IDS = (
    "crewai_backend_tool_stream",
    "langgraph_stream_text",
    "adk_stream_text",
    "delta_assembly",
    "cumulative_assembly",
    "final_only",
    "done_before_eof",
    "terminal_error",
    "incomplete_eof",
    "cancellation_recovery",
)
FRAMEWORK_BY_CASE = {
    "crewai_backend_tool_stream": "crewai",
    "langgraph_stream_text": "langgraph",
    "adk_stream_text": "google_adk",
}
AG_UI_REPOSITORY = "https://github.com/ag-ui-protocol/ag-ui.git"
AG_UI_COMMIT = "2b967c34c49759c4afe0f0a176b281a0f645de34"
MODEL = "gemini-3.7-flash"


@dataclass(frozen=True)
class Receipt:
    case_id: str
    status: str
    reason: str
    framework: str | None
    response_characters: int
    stream_events: int
    terminal: str
    source: dict[str, Any] | None = None


def _receipt(
    case_id: str,
    *,
    passed: bool,
    reason: str = "ok",
    framework: str | None = None,
    response_characters: int = 0,
    stream_events: int = 0,
    terminal: str = "none",
    source: dict[str, Any] | None = None,
) -> Receipt:
    return Receipt(
        case_id=case_id,
        status="passed" if passed else "failed",
        reason=reason if not passed else "ok",
        framework=framework,
        response_characters=max(0, response_characters),
        stream_events=max(0, stream_events),
        terminal=terminal,
        source=source if passed else None,
    )


def _withheld(case_id: str, reason: str = "not_reached") -> Receipt:
    return Receipt(
        case_id,
        "withheld",
        reason,
        FRAMEWORK_BY_CASE.get(case_id),
        0,
        0,
        "none",
    )


def _event(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


@contextmanager
def fixture_server() -> Iterator[tuple[str, dict[str, Any]]]:
    state: dict[str, Any] = {
        "cancel_started": threading.Event(),
        "disconnected": threading.Event(),
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            case = body.get("message", "delta")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if case == "cancel":
                self.wfile.write(_event({"type": "TEXT", "text": "partial"}))
                self.wfile.flush()
                state["cancel_started"].set()
                self.connection.settimeout(4)
                try:
                    if self.connection.recv(1) == b"":
                        state["disconnected"].set()
                except (ConnectionError, socket.timeout):
                    pass
                return
            if case == "error":
                self.wfile.write(_event({"type": "RUN_ERROR"}))
                self.wfile.flush()
                return
            if case == "eof":
                self.wfile.write(_event({"type": "TEXT", "text": "partial"}))
                self.wfile.flush()
                self.close_connection = True
                return
            if case == "cumulative":
                events = [
                    {"type": "TEXT", "text": "hel"},
                    {"type": "TEXT", "text": "hello"},
                    {"type": "RUN_FINISHED"},
                ]
            elif case == "final":
                events = [
                    {"type": "TEXT", "text": "ignored"},
                    {"type": "RUN_FINISHED", "answer": "hello"},
                ]
            else:
                events = [
                    {"type": "TEXT", "text": "hel"},
                    {"type": "TEXT", "text": "lo"},
                    {"type": "RUN_FINISHED"},
                ]
            for event in events:
                self.wfile.write(_event(event))
            self.wfile.flush()
            if case == "hold":
                time.sleep(5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _trace(response: Any) -> dict[str, Any]:
    return dict((response.metadata or {}).get("external_agent") or {})


def _input(message: str) -> Any:
    from fi.simulate.agent.wrapper import AgentInput

    execution_id = uuid.uuid4().hex
    return AgentInput(
        thread_id=f"sse-{execution_id}",
        execution_id=execution_id,
        turn_index=0,
        messages=[],
        new_message={"role": "user", "content": message},
    )


def _wrapper(endpoint: str, *, mode: str = "delta", final: bool = False) -> Any:
    from fi.simulate.agent.wrappers.http import HTTPAgentWrapper

    text = (
        [{"mode": "final", "path": "data.answer"}]
        if final
        else [
            {
                "mode": mode,
                "path": "data.text",
                "when": {"path": "data.type", "values": ["TEXT"]},
            }
        ]
    )
    return HTTPAgentWrapper(
        endpoint=endpoint,
        protocol="json_template",
        request_template={"message": "{{new_message_content}}"},
        sse={
            "completion": {"path": "data.type", "values": ["RUN_FINISHED"]},
            "failure": {"path": "data.type", "values": ["RUN_ERROR"]},
            "text": text,
        },
        timeout=12,
        secret_values={},
    )


async def run_transport_cases() -> list[Receipt]:
    receipts: list[Receipt] = []
    with fixture_server() as (base_url, state):
        for case_id, message, mode, final in (
            ("delta_assembly", "delta", "delta", False),
            ("cumulative_assembly", "cumulative", "cumulative", False),
            ("final_only", "final", "delta", True),
        ):
            response = await _wrapper(base_url, mode=mode, final=final).call(
                _input(message)
            )
            trace = _trace(response)
            receipts.append(
                _receipt(
                    case_id,
                    passed=response.content == "hello" and trace.get("success") is True,
                    reason="unexpected_result",
                    response_characters=len(response.content),
                    stream_events=int(trace.get("stream_events_received") or 0),
                    terminal="RUN_FINISHED",
                )
            )

        started = time.monotonic()
        response = await _wrapper(base_url).call(_input("hold"))
        elapsed = time.monotonic() - started
        trace = _trace(response)
        receipts.append(
            _receipt(
                "done_before_eof",
                passed=response.content == "hello"
                and trace.get("success") is True
                and elapsed < 3,
                reason="waited_for_eof",
                response_characters=len(response.content),
                stream_events=int(trace.get("stream_events_received") or 0),
                terminal="RUN_FINISHED",
            )
        )

        response = await _wrapper(base_url).call(_input("error"))
        trace = _trace(response)
        receipts.append(
            _receipt(
                "terminal_error",
                passed=trace.get("success") is False
                and trace.get("stream_reason") == "sse_application",
                reason="error_not_propagated",
                stream_events=int(trace.get("stream_events_received") or 0),
                terminal="RUN_ERROR",
            )
        )

        response = await _wrapper(base_url).call(_input("eof"))
        trace = _trace(response)
        receipts.append(
            _receipt(
                "incomplete_eof",
                passed=trace.get("success") is False
                and trace.get("stream_reason") == "sse_incomplete",
                reason="incomplete_stream_accepted",
                stream_events=int(trace.get("stream_events_received") or 0),
                terminal="none",
            )
        )

        wrapper = _wrapper(base_url)
        task = asyncio.create_task(wrapper.call(_input("cancel")))
        await asyncio.to_thread(state["cancel_started"].wait, 3)
        task.cancel()
        cancelled = False
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        disconnected = await asyncio.to_thread(state["disconnected"].wait, 3)
        recovered = await _wrapper(base_url).call(_input("delta"))
        recovered_trace = _trace(recovered)
        receipts.append(
            _receipt(
                "cancellation_recovery",
                passed=cancelled
                and disconnected
                and recovered.content == "hello"
                and recovered_trace.get("success") is True,
                reason="cancellation_not_observed",
                response_characters=len(recovered.content),
                stream_events=int(recovered_trace.get("stream_events_received") or 0),
                terminal="RUN_FINISHED",
            )
        )
    return receipts


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=900,
    )
    if completed.returncode:
        raise RuntimeError("framework_setup")


def _installed_versions(python: Path, packages: tuple[str, ...]) -> dict[str, str]:
    code = (
        "import importlib.metadata as m,json,sys; "
        "print(json.dumps({name:m.version(name) for name in sys.argv[1:]}))"
    )
    output = subprocess.check_output(
        [str(python), "-c", code, *packages], text=True, timeout=30
    )
    return json.loads(output)


def prepare_upstream(work: Path) -> tuple[Path, str, dict[str, dict[str, str]]]:
    work.mkdir(parents=True, exist_ok=True)
    source = work / "ag-ui"
    if source.exists():
        shutil.rmtree(source)
    _run(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            AG_UI_REPOSITORY,
            str(source),
        ]
    )
    _run(["git", "checkout", "--detach", AG_UI_COMMIT], cwd=source)
    if (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip()
        != AG_UI_COMMIT
    ):
        raise RuntimeError("source_revision")

    adk_server = (
        source / "integrations/adk-middleware/python/examples/server/__init__.py"
    )
    adk_agent = (
        source
        / "integrations/adk-middleware/python/examples/server/api/agentic_chat.py"
    )
    adk_text = adk_agent.read_text(encoding="utf-8")
    if adk_text.count('model="gemini-2.5-flash"') != 1:
        raise RuntimeError("adk_patch_shape")
    adk_agent.write_text(
        adk_text.replace('model="gemini-2.5-flash"', f'model="{MODEL}"'),
        encoding="utf-8",
    )
    adk_server.write_text(
        "from server.api.agentic_chat import app\n"
        "import os, uvicorn\n"
        "def main():\n"
        "    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')))\n",
        encoding="utf-8",
    )
    patch_hash = hashlib.sha256(
        adk_agent.read_bytes() + adk_server.read_bytes()
    ).hexdigest()

    projects = (
        source / "integrations/crew-ai/python/examples",
        source / "integrations/langgraph/python/examples",
        source / "integrations/adk-middleware/python/examples",
    )
    for project in projects:
        _run(["uv", "sync", "--project", str(project)])
    crew_python = projects[0] / ".venv/bin/python"
    _run(["uv", "pip", "install", "--python", str(crew_python), "google-auth"])
    versions = {
        "crewai": _installed_versions(
            crew_python, ("crewai", "ag-ui-crewai", "ag-ui-protocol")
        ),
        "langgraph": _installed_versions(
            projects[1] / ".venv/bin/python",
            ("langgraph", "ag-ui-langgraph", "ag-ui-protocol"),
        ),
        "google_adk": _installed_versions(
            projects[2] / ".venv/bin/python",
            ("google-adk", "ag-ui-adk", "ag-ui-protocol"),
        ),
    }
    return source, patch_hash, versions


@contextmanager
def framework_server(
    command: list[str], *, cwd: Path, env: dict[str, str], port: int
) -> Iterator[None]:
    log = (cwd / f"qualification-{port}.log").open("wb")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env={**os.environ, **env, "PORT": str(port)},
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("framework_start")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("framework_start_timeout")
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def _agui_wrapper(endpoint: str) -> Any:
    from fi.simulate.agent.wrappers.http import HTTPAgentWrapper

    return HTTPAgentWrapper(
        endpoint=endpoint,
        protocol="json_template",
        request_template={
            "threadId": "{{thread_id}}",
            "runId": "{{execution_id}}-{{turn_index}}",
            "state": {},
            "messages": [
                {
                    "id": "user-{{execution_id}}-{{turn_index}}",
                    "role": "user",
                    "content": "{{new_message_content}}",
                }
            ],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        },
        sse={
            "completion": {"path": "data.type", "values": ["RUN_FINISHED"]},
            "failure": {"path": "data.type", "values": ["RUN_ERROR"]},
            "text": [
                {
                    "mode": "delta",
                    "path": "data.delta",
                    "when": {
                        "path": "data.type",
                        "values": ["TEXT_MESSAGE_CONTENT"],
                    },
                },
                {
                    "mode": "cumulative",
                    "path": "data.messages.-1.content",
                    "when": {
                        "path": "data.messages.-1.role",
                        "values": ["assistant"],
                    },
                },
            ],
        },
        timeout=180,
        secret_values={},
    )


def _framework_source(
    framework: str,
    subdirectory: str,
    endpoint: str,
    patch_hash: str,
    versions: dict[str, str],
    model: str,
) -> dict[str, Any]:
    return {
        "repository": AG_UI_REPOSITORY,
        "commit": AG_UI_COMMIT,
        "subdirectory": subdirectory,
        "framework": framework,
        "model": model,
        "endpoint": endpoint,
        "provider_patch_sha256": patch_hash,
        "versions": versions,
    }


async def run_framework_cases(work: Path) -> list[Receipt]:
    try:
        source, patch_hash, versions = await asyncio.to_thread(prepare_upstream, work)
    except Exception:
        return [
            _receipt(case_id, passed=False, reason="framework_setup", framework=name)
            for case_id, name in zip(
                CASE_IDS[:3], ("crewai", "langgraph", "google_adk")
            )
        ]

    project = os.environ["VERTEXAI_PROJECT"]
    credentials = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    common_env = {
        "GOOGLE_APPLICATION_CREDENTIALS": credentials,
        "GOOGLE_CLOUD_PROJECT": project,
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "VERTEXAI_PROJECT": project,
        "VERTEXAI_LOCATION": "global",
    }
    receipts: list[Receipt] = []

    crew = source / "integrations/crew-ai/python/examples"
    crew_endpoint = "/conversational_flows/backend_tool_rendering"
    try:
        with framework_server(
            [
                str(crew / ".venv/bin/python"),
                "-c",
                "from agents.dojo import main; main()",
            ],
            cwd=crew,
            env={**common_env, "MODEL": f"vertex_ai/{MODEL}"},
            port=8101,
        ):
            wrapper = _agui_wrapper(f"http://127.0.0.1:8101{crew_endpoint}")
            response = await wrapper.call(
                _input(
                    "Ask for the weather in Pune and use the get_weather tool.",
                )
            )
            trace = _trace(response)
            passed = (
                trace.get("success") is True
                and bool(response.content)
                and int(trace.get("stream_events_received") or 0) >= 5
            )
            receipts.append(
                _receipt(
                    "crewai_backend_tool_stream",
                    passed=passed,
                    reason="stream_text_not_observed",
                    framework="crewai",
                    response_characters=len(response.content),
                    stream_events=int(trace.get("stream_events_received") or 0),
                    terminal="RUN_FINISHED",
                    source=_framework_source(
                        "crewai",
                        "integrations/crew-ai/python",
                        crew_endpoint,
                        patch_hash,
                        versions["crewai"],
                        MODEL,
                    ),
                )
            )
    except Exception:
        receipts.append(
            _receipt(
                "crewai_backend_tool_stream",
                passed=False,
                reason="framework_runtime",
                framework="crewai",
            )
        )

    langgraph = source / "integrations/langgraph/python/examples"
    langgraph_endpoint = "/agent/agentic_chat"
    try:
        langgraph_server = """
import os
import uvicorn
from fastapi import FastAPI
from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

def reply(_state):
    return {"messages": [AIMessage(content="LANGGRAPH_OK")]}

builder = StateGraph(MessagesState)
builder.add_node("reply", reply)
builder.add_edge(START, "reply")
builder.add_edge("reply", END)
graph = builder.compile(checkpointer=MemorySaver())
app = FastAPI()
agent = LangGraphAgent(name="agentic_chat", description="ALK qualification", graph=graph)
add_langgraph_fastapi_endpoint(app=app, agent=agent, path="/agent/agentic_chat")
uvicorn.run(app, host="0.0.0.0", port=int(os.environ["PORT"]))
"""
        with framework_server(
            [str(langgraph / ".venv/bin/python"), "-c", langgraph_server],
            cwd=langgraph,
            env=common_env,
            port=8102,
        ):
            response = await _agui_wrapper(
                f"http://127.0.0.1:8102{langgraph_endpoint}"
            ).call(_input("Reply exactly LANGGRAPH_OK."))
            trace = _trace(response)
            receipts.append(
                _receipt(
                    "langgraph_stream_text",
                    passed=trace.get("success") is True
                    and "langgraph_ok" in response.content.lower(),
                    reason="stream_text_not_observed",
                    framework="langgraph",
                    response_characters=len(response.content),
                    stream_events=int(trace.get("stream_events_received") or 0),
                    terminal="RUN_FINISHED",
                    source=_framework_source(
                        "langgraph",
                        "integrations/langgraph/python",
                        langgraph_endpoint,
                        patch_hash,
                        versions["langgraph"],
                        "deterministic",
                    ),
                )
            )
    except Exception:
        receipts.append(
            _receipt(
                "langgraph_stream_text",
                passed=False,
                reason="framework_runtime",
                framework="langgraph",
            )
        )

    adk = source / "integrations/adk-middleware/python/examples"
    adk_endpoint = "/"
    try:
        with framework_server(
            [
                str(adk / ".venv/bin/python"),
                "-c",
                "from server import main; main()",
            ],
            cwd=adk,
            env=common_env,
            port=8103,
        ):
            response = await _agui_wrapper("http://127.0.0.1:8103/").call(
                _input("Reply exactly ADK_OK.")
            )
            trace = _trace(response)
            receipts.append(
                _receipt(
                    "adk_stream_text",
                    passed=trace.get("success") is True
                    and "adk_ok" in response.content.lower(),
                    reason="stream_text_not_observed",
                    framework="google_adk",
                    response_characters=len(response.content),
                    stream_events=int(trace.get("stream_events_received") or 0),
                    terminal="RUN_FINISHED",
                    source=_framework_source(
                        "google_adk",
                        "integrations/adk-middleware/python",
                        adk_endpoint,
                        patch_hash,
                        versions["google_adk"],
                        MODEL,
                    ),
                )
            )
    except Exception:
        receipts.append(
            _receipt(
                "adk_stream_text",
                passed=False,
                reason="framework_runtime",
                framework="google_adk",
            )
        )
    return receipts


def imported_provenance() -> dict[str, Any]:
    import fi.simulate.agent.wrappers.http_stream as http_stream

    imported = Path(http_stream.__file__).resolve()
    branch = Path(os.environ["ALK_BRANCH_ROOT"]).resolve()
    expected = os.environ["EXPECTED_ALK_SSE_SHA256"]
    actual = hashlib.sha256(imported.read_bytes()).hexdigest()
    return {
        "expected_sha256": expected,
        "imported_sha256": actual,
        "imported_from_branch": imported.is_relative_to(branch),
    }


async def qualify(output: Path, work: Path) -> int:
    provenance = imported_provenance()
    receipts = {case_id: _withheld(case_id) for case_id in CASE_IDS}
    if (
        provenance["expected_sha256"] == provenance["imported_sha256"]
        and provenance["imported_from_branch"]
    ):
        try:
            for receipt in await run_transport_cases():
                receipts[receipt.case_id] = receipt
        except Exception:
            for case_id in CASE_IDS[3:]:
                receipts[case_id] = _receipt(
                    case_id, passed=False, reason="runner_exception"
                )
        try:
            for receipt in await run_framework_cases(work):
                receipts[receipt.case_id] = receipt
        except Exception:
            for case_id in CASE_IDS[:3]:
                receipts[case_id] = _receipt(
                    case_id,
                    passed=False,
                    reason="runner_exception",
                    framework=FRAMEWORK_BY_CASE[case_id],
                )
    else:
        receipts = {
            case_id: _withheld(case_id, "provenance_mismatch") for case_id in CASE_IDS
        }
    ordered = [asdict(receipts[case_id]) for case_id in CASE_IDS]
    report = {
        "schema_version": "futureagi.sse-e2b-qualification.v1",
        "provenance": provenance,
        "receipts": ordered,
        "passed": all(item["status"] == "passed" for item in ordered),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(qualify(args.output, args.work)))


if __name__ == "__main__":
    main()
