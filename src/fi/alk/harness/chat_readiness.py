"""Private chat ingress and workflow readiness checks."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fi._diagnostics import _SECRET_FIELD, sanitize_diagnostic
from fi.simulate.agent.wrapper import AgentInput

from .chat_transport import create_chat_wrapper
from .diagnostics import HarnessDiagnostic
from .job import HarnessStage


def _failure_code(trace):
    if trace.get("timeout_error") is True:
        return "chat_ingress_timeout"
    status = trace.get("status_code", 0)
    stage = trace.get("failure_stage")
    if stage in {"setup", "http"} and status in {400, 404, 405, 415, 422}:
        return "chat_ingress_configuration_invalid"
    if status in {401, 403}:
        return "credential_missing"
    if (
        status in {408, 429, 502, 503, 504}
        or trace.get("network_error")
        or (stage == "tool_events" and 500 <= status < 600)
    ):
        return "external_service_unavailable"
    if stage == "tool_events":
        return "chat_tool_evidence_unverified"
    return "chat_ingress_failed"


def _usable_tool_call(call):
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = call.get("name") or function.get("name")
    arguments = call.get("arguments", function.get("arguments", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return False
    return isinstance(name, str) and bool(name.strip()) and isinstance(arguments, dict)


async def probe_chat_ingress(
    *,
    interface,
    endpoint,
    document=None,
    opening_message=None,
    authoring: Path,
    candidate_hash,
    bundle_digest,
    secret_values,
    redaction_values,
    tools=(),
    scenario_id=None,
    scenario_key=None,
    timeout=120.0,
    local_auth_values=None,
):
    from .authoring_runtime_validation import RuntimeValidationError

    redaction_values = {**(redaction_values or {}), **(local_auth_values or {})}
    attempt = uuid4().hex
    key = scenario_key or (document or {}).get("scenario_key") or attempt
    persona = (document or {}).get("persona") or {}
    opening = opening_message or (persona.get("initial_message") if isinstance(persona, dict) else None)
    message = {
        "role": "user",
        "content": opening if isinstance(opening, str) and opening.strip() else "Hello",
    }
    receipt = {
        "attempt": attempt,
        "candidate_hash": candidate_hash,
        "bundle_digest": bundle_digest,
        "status": "failed",
        "timeout_seconds": timeout,
    }
    if document is not None or scenario_key is not None:
        receipt["scenario_key"] = key
    try:
        wrapper = create_chat_wrapper(
            interface,
            endpoint,
            timeout=timeout,
            total_timeout=timeout,
            secret_values=secret_values,
            local_auth_values=local_auth_values,
        )
        response = await wrapper.call(
            AgentInput(
                thread_id=key,
                execution_id=scenario_id or key,
                modality="text",
                tools=list(tools),
                scenario_name=key,
                turn_index=0,
                messages=[message],
                new_message=message,
            )
        )
        trace = (response.metadata or {}).get("external_agent", {})
        receipt["trace"] = {
            name: trace.get(name)
            for name in (
                "status_code",
                "failure_stage",
                "timeout_error",
                "latency_ms",
                "error",
                "response_excerpt",
            )
        }
        valid_tools = all(_usable_tool_call(call) for call in response.tool_calls or [])
        if (
            not trace.get("success")
            or not valid_tools
            or not (response.content.strip() or response.tool_calls)
        ):
            code = (
                _failure_code(trace)
                if not trace.get("success")
                else "chat_ingress_failed"
            )
            detail = trace.get("error") or (
                "Chat returned malformed tool calls"
                if not valid_tools
                else "Chat returned no text or tool calls"
            )
            if code == "chat_ingress_timeout":
                detail = f"Chat ingress request timed out with a {timeout:g}s deadline"
            excerpt = trace.get("response_excerpt")
            if excerpt:
                detail = (
                    f"{detail}\n<untrusted_response>\n{excerpt}\n</untrusted_response>"
                )
            diagnostic = HarnessDiagnostic.create(
                stage=HarnessStage.CONNECTING_AGENT,
                component="chat_ingress",
                code=code,
                message=sanitize_diagnostic(str(detail), redaction_values)[:4000],
            )
            receipt["diagnostic"] = diagnostic.model_dump(mode="json")
            raise RuntimeValidationError(
                "environment", diagnostic.redacted_message, diagnostics=(diagnostic,)
            )
        receipt["status"] = "passed"
    except RuntimeValidationError:
        raise
    except Exception as exc:
        diagnostic = HarnessDiagnostic.create(
            stage=HarnessStage.CONNECTING_AGENT,
            component="chat_ingress",
            code="chat_ingress_failed",
            message=sanitize_diagnostic(str(exc), redaction_values)[:4000],
        )
        receipt["diagnostic"] = diagnostic.model_dump(mode="json")
        raise RuntimeValidationError(
            "environment", diagnostic.redacted_message, diagnostics=(diagnostic,)
        ) from exc
    except asyncio.CancelledError:
        receipt["status"] = "cancelled"
        raise
    finally:
        folder = authoring / "generic-harness" / "chat-readiness"
        folder.mkdir(parents=True, exist_ok=True)

        # Redact evidence before bounding; controller identities must remain exact.
        if "trace" in receipt:
            for name in ("error", "response_excerpt"):
                if isinstance(receipt["trace"].get(name), str):
                    receipt["trace"][name] = sanitize_diagnostic(
                        receipt["trace"][name], redaction_values
                    )[:4000]
        (folder / f"{attempt}.json").write_text(json.dumps(receipt, indent=2) + "\n")


class _PrivateWorkflowArtifacts:
    def __init__(self, folder, redactions):
        self.folder = folder
        self.redactions = redactions
        self.trace = {}
        self.transcript = ""
        self.paths = []

    async def upload_artifact(self, data, *, kind, scenario_key=None, deadline=None):
        from .outbound import ArtifactKind

        def scrub(value):
            if isinstance(value, dict):
                return {
                    scrub(key): "<redacted>" if _SECRET_FIELD.search(key) else scrub(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [scrub(item) for item in value]
            text = value if isinstance(value, str) else json.dumps(value)
            redacted = sanitize_diagnostic(text, self.redactions, limit=max(8192, len(data) * 10))
            if not isinstance(value, str):
                try:
                    return json.loads(redacted)
                except ValueError:
                    pass
            return redacted

        # Redact parsed values so replacement quotes and credential literals cannot break JSON.
        if kind == ArtifactKind.TOOL_TRACE:
            text = "\n".join(json.dumps(scrub(json.loads(line))) for line in data.splitlines() if line.strip())
        else:
            text = json.dumps(scrub(json.loads(data)))
        path = self.folder / f"{kind.value}-{uuid4().hex}.json"
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        self.paths.append(path.name)
        if kind == ArtifactKind.TRACE:
            self.trace = json.loads(data)
        elif kind == ArtifactKind.TRANSCRIPT:
            self.transcript = text
        return str(path)


TOOL_ROUTING = "tool-routing-observations.json"


def record_tool_routing(authoring: Path, scenario_key: str, calls) -> None:
    """Every tool call the qualification conversation made that reached the test world,
    beside the tool certification: what readiness reads as observed routing, per tool.

    The calls are the world's own record of what arrived at it, so each one was routed to
    the world; a tool never called here is simply absent."""
    tools: dict[str, dict] = {}
    for call in calls:
        name = str(getattr(call, "name", "") or "")
        if not name:
            continue
        outcome = (
            "refused"
            if getattr(call, "refused", False)
            else "ok" if getattr(call, "ok", True) else "error"
        )
        seen = tools.setdefault(name, {"tool": name, "routed": True, "calls": 0, "outcomes": []})
        seen["calls"] += 1
        if outcome not in seen["outcomes"]:
            seen["outcomes"].append(outcome)
    path = authoring / "generic-harness" / TOOL_ROUTING
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "qualification_conversation",
                "scenario_key": scenario_key,
                "tools": sorted(tools.values(), key=lambda one: one["tool"]),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _reported_calls(reply) -> set[str]:
    """The tool names the agent's own reply says it called."""
    names: set[str] = set()
    for call in getattr(reply, "tool_calls", None) or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = call.get("name") or function.get("name")
        if name:
            names.add(str(name))
    return names


def _tool_request(tool) -> str:
    """A non-destructive request for one tool, built from its contract."""
    values = {
        arg: (tool.arg_values.get(arg) or [None])[0]
        if isinstance(tool.arg_values.get(arg), list)
        else tool.arg_values.get(arg)
        for arg in tool.args
    }
    shown = ", ".join(f"{arg}={value!r}" for arg, value in values.items() if value is not None)
    about = f" ({tool.description.strip()})" if tool.description.strip() else ""
    return (
        f"Please use your {tool.name} tool{about} now"
        + (f" with {shown}" if shown else "")
        + ". This is a read-only check: only look the answer up and tell me what it returns."
    )


async def request_uncovered_tools(
    *,
    interface,
    endpoint,
    runtime,
    contract,
    authoring: Path,
    secret_values,
    local_auth_values=None,
    timeout=120.0,
) -> None:
    """Ask the agent once for each tool the qualification conversation did not call.

    Only a tool whose contract names no effect is asked: a request for one that changes state
    could change the world, so it stays unobserved with that reason. Each request is a fresh
    one-message conversation; the tool counts as routed only when the world's own trace shows
    the call. The results join the routing observations. Never raises.
    """
    from .call_runner import _collect_file_tool_calls

    path = authoring / "generic-harness" / TOOL_ROUTING
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    seen = {one["tool"] for one in body.get("tools") or []}
    unasked: list[dict] = []
    for tool in getattr(contract, "tools", None) or []:
        if tool.name in seen:
            continue
        if (tool.effects or "").strip():
            unasked.append({"tool": tool.name, "reason": "changes_state"})
            continue
        try:
            before = sum(
                1 for call in _collect_file_tool_calls(runtime) if call.name == tool.name
            )
        except Exception:  # noqa: BLE001 - no trace to read, nothing can be observed
            unasked.append({"tool": tool.name, "reason": "not_called"})
            continue
        message = {"role": "user", "content": _tool_request(tool)}
        try:
            wrapper = create_chat_wrapper(
                interface,
                endpoint,
                timeout=timeout,
                total_timeout=timeout,
                secret_values=secret_values,
                local_auth_values=local_auth_values,
            )
            key = f"tool-request-{uuid4().hex}"
            reply = await wrapper.call(
                AgentInput(
                    thread_id=key,
                    execution_id=key,
                    modality="text",
                    tools=[],
                    scenario_name=key,
                    turn_index=0,
                    messages=[message],
                    new_message=message,
                )
            )
        except Exception:  # noqa: BLE001 - an unanswered request leaves the tool unobserved
            unasked.append({"tool": tool.name, "reason": "request_failed"})
            continue
        calls = [call for call in _collect_file_tool_calls(runtime) if call.name == tool.name]
        if len(calls) > before:
            outcomes = sorted(
                {
                    "refused"
                    if getattr(call, "refused", False)
                    else "ok" if getattr(call, "ok", True) else "error"
                    for call in calls[before:]
                }
            )
            body["tools"].append(
                {
                    "tool": tool.name,
                    "routed": True,
                    "calls": len(calls) - before,
                    "outcomes": outcomes,
                    "source": "tool_request",
                }
            )
        elif tool.name in _reported_calls(reply):
            # The agent says it called the tool, but the world never saw the call: it
            # went somewhere else.
            body["tools"].append(
                {
                    "tool": tool.name,
                    "routed": False,
                    "calls": 0,
                    "outcomes": [],
                    "source": "tool_request",
                    "reason": "reported_not_seen",
                }
            )
        else:
            unasked.append({"tool": tool.name, "reason": "not_called"})
    body["tools"] = sorted(body["tools"], key=lambda one: one["tool"])
    body["unobserved"] = sorted(unasked, key=lambda one: one["tool"])
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def probe_chat_workflow(
    *,
    job,
    source: Path,
    bundle: Path,
    authoring: Path,
    runtime,
    scenario,
    candidate_hash,
    bundle_digest,
    secret_values,
    redaction_values,
    timeout=300.0,
):
    """Exercise a private conversation and require observed tool execution evidence."""
    from .authoring_runtime_validation import RuntimeValidationError
    from .call_runner import CallRunnerContext
    from .chat_call_runner import HostedChatCallRunner
    from .hosted_scheduler import CallAborted

    redactions = {
        **(secret_values or {}),
        **(redaction_values or {}),
        **getattr(runtime, "_local_auth_values", {}),
    }
    attempt = uuid4().hex
    folder = authoring / "generic-harness" / "chat-readiness" / attempt
    folder.mkdir(parents=True, mode=0o700)
    artifacts = _PrivateWorkflowArtifacts(folder, redactions)
    receipt = {
        "attempt": attempt,
        "candidate_hash": candidate_hash,
        "bundle_digest": bundle_digest,
        "scenario_key": scenario.scenario_key,
        "status": "failed",
        "timeout_seconds": timeout,
        "tool_call_count": 0,
        "artifacts": artifacts.paths,
    }

    def failure(code, message):
        detail = sanitize_diagnostic(str(message), redactions)[:4000]
        if artifacts.transcript:
            detail += "\n<untrusted_transcript>\n" + artifacts.transcript[:6000] + "\n</untrusted_transcript>"
        diagnostic = HarnessDiagnostic.create(
            stage=HarnessStage.CONNECTING_AGENT,
            component="chat_ingress",
            code=code,
            message=detail,
        )
        receipt["diagnostic"] = diagnostic.model_dump(mode="json")
        return RuntimeValidationError(
            "environment", diagnostic.redacted_message, diagnostics=(diagnostic,)
        )

    async def retain_interrupted_conversation(exc):
        from dataclasses import asdict

        from .call_runner import _collect_file_tool_calls
        from .outbound import ArtifactKind

        transcript = getattr(exc, "partial_transcript", None)
        if transcript is None:
            return
        if not transcript.calls:
            transcript.calls.extend(_collect_file_tool_calls(runtime))
        receipt["tool_call_count"] = len(transcript.calls)
        receipt["turns"] = len(transcript.exchanges)
        await artifacts.upload_artifact(transcript.artifact(), kind=ArtifactKind.TRANSCRIPT)
        if transcript.calls:
            trace = "\n".join(
                json.dumps(asdict(call), sort_keys=True, default=str)
                for call in transcript.calls
            ).encode("utf-8")
            await artifacts.upload_artifact(trace, kind=ArtifactKind.TOOL_TRACE)

    try:
        runner = HostedChatCallRunner(
            artifacts,
            CallRunnerContext(
                job=job,
                bundle_dir=bundle,
                work_directory=folder,
                evidence_seam=None,
                target_provider_secret_values=secret_values,
                attempt_number=1,
                source_directory=source,
            ),
        )
        async with asyncio.timeout(timeout):
            outcome = await runner.run(scenario, runtime)
        receipt["tool_call_count"] = len(outcome.calls)
        receipt["turns"] = outcome.turns
        from .contract import has_executable_tool_workflow

        requires_tool_evidence = getattr(job, "require_tool_workflow", False) or (
            has_executable_tool_workflow(runner._contract, include_construct=False)
        )
        if not outcome.calls and requires_tool_evidence:
            raise failure(
                "chat_tool_evidence_unverified",
                "Required tool evidence was not captured. Inspect source-supported "
                "observation mapping. This may be target noncompliance; do not "
                "fabricate evidence or change business logic.",
            )
        if not outcome.calls:
            receipt["tool_evidence_status"] = "not_observed"
        record_tool_routing(authoring, scenario.scenario_key, outcome.calls)
        receipt["status"] = "passed"
    except RuntimeValidationError:
        raise
    except TimeoutError as exc:
        await retain_interrupted_conversation(exc.__cause__)
        raise failure(
            "chat_ingress_timeout", f"Private chat workflow exceeded its {timeout:g}s deadline"
        ) from exc
    except CallAborted as exc:
        if exc.partial is not None:
            receipt["tool_call_count"] = len(exc.partial.calls)
            receipt["turns"] = exc.partial.turns
        raise failure(_failure_code(artifacts.trace), str(exc)) from exc
    except asyncio.CancelledError as exc:
        receipt["status"] = "cancelled"
        await retain_interrupted_conversation(exc)
        raise
    except Exception as exc:
        raise failure("chat_ingress_failed", str(exc)) from exc
    finally:
        path = folder / "receipt.json"
        path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
