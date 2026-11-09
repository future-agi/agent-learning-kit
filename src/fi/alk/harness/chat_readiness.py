"""Private chat ingress and workflow readiness checks."""

import asyncio
import json
import time
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


def world_call_log(runtime, since=0.0):
    """The calls the test world itself received since ``since``: its call log, written by
    the world's own surfaces (the tool proxy in front of a tools service, the MCP toolbox),
    never by the agent."""
    from .call_runner import _collect_tool_trace_calls

    try:
        return [call for call in _collect_tool_trace_calls(runtime) if call.at >= since]
    except Exception:  # noqa: BLE001 - no log to read, nothing received
        return []


def _logged_tools(contract, runtime, bundle: Path | None = None) -> set[str]:
    """Tools the world logs every call of: a service tool behind the tool proxy, and a tool
    the bundle's MCP toolbox serves. Any other tool runs inside the agent, so the world holds
    its data but no record of the call."""
    endpoints = getattr(runtime, "endpoints", None) or {}
    logged: set[str] = set()
    if "tool_proxy" in endpoints:
        logged |= {e.tool for e in contract.tool_entrypoints if e.mode == "service"}
    if "mcp_toolbox" in endpoints and bundle is not None:
        try:
            served = json.loads(
                (bundle / "generated" / "mcp-toolbox" / "toolbox.json").read_text(encoding="utf-8")
            ).get("tools")
        except (OSError, ValueError, AttributeError):
            served = None
        logged |= set(served) if isinstance(served, dict) else set()
    return logged


def _tool_names(contract) -> dict[str, str]:
    """Each tool by the name its calls carry: its own, or for a service tool the last
    segment of its path, which is what the proxy logs."""
    names = {tool.name: tool.name for tool in contract.tools}
    for entry in contract.tool_entrypoints:
        segment = entry.endpoint.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if entry.mode == "service" and segment:
            names.setdefault(segment, entry.tool)
    return names


def _outcome(call) -> str:
    if getattr(call, "refused", False):
        return "refused"
    return "ok" if getattr(call, "ok", True) else "error"


def _routing(contract, runtime, *, received, reported, bundle=None):
    """Routing read from the world's side only. A tool the world received is routed. A tool
    the agent reports calling that the world logs but never received is not routed. Any
    other reported tool runs inside the agent, so its routing cannot be observed."""
    names = _tool_names(contract)
    arrived: dict[str, list] = {}
    for call in received:
        tool = names.get(str(getattr(call, "name", "") or ""))
        if tool:
            arrived.setdefault(tool, []).append(call)
    tools = [
        {
            "tool": tool,
            "routed": True,
            "calls": len(calls),
            "outcomes": sorted({_outcome(call) for call in calls}),
        }
        for tool, calls in arrived.items()
    ]
    unobserved = []
    logged = _logged_tools(contract, runtime, bundle)
    claimed = {names.get(str(getattr(call, "name", "") or "")) for call in reported}
    for tool in sorted(claimed - set(arrived) - {None}):
        if tool in logged:
            tools.append({"tool": tool, "routed": False, "reason": "not_received"})
        else:
            unobserved.append({"tool": tool, "reason": "no_world_record"})
    return tools, unobserved


def _write_routing(authoring: Path, body) -> None:
    body["tools"] = sorted(body["tools"], key=lambda one: one["tool"])
    body["unobserved"] = sorted(body["unobserved"], key=lambda one: one["tool"])
    path = authoring / "generic-harness" / TOOL_ROUTING
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_tool_routing(
    authoring: Path, scenario_key: str, contract, runtime, *, received, reported, bundle=None
) -> None:
    """Which tools the qualification conversation routed to the test world, beside the tool
    certification: what readiness reads as observed routing, per tool. ``received`` is what
    the world got (its call log and the calls the harness ran there); ``reported`` is every
    call the conversation shows, including those only the agent says it made."""
    tools, unobserved = _routing(
        contract, runtime, received=received, reported=reported, bundle=bundle
    )
    _write_routing(
        authoring,
        {
            "schema_version": 1,
            "source": "world_call_log",
            "scenario_key": scenario_key,
            "tools": tools,
            "unobserved": unobserved,
        },
    )


_REACH_FIELDS = ("dsn_env", "config_key", "host", "port", "database", "loader_module")


def _outside_world(contract) -> set[str]:
    """Tools the contract says cannot be routed to the test world: no reachable
    implementation, or a dependency the environment cannot stand up a copy of."""
    document = contract.model_dump(mode="json") if hasattr(contract, "model_dump") else {}
    names = {
        str(entry.get("tool") or "")
        for entry in document.get("tool_entrypoints") or []
        if entry.get("mode") == "unreachable"
        or (entry.get("mode") == "service" and not entry.get("service"))
    }
    for dependency in document.get("dependencies") or []:
        reached = dependency.get("reached") or {}
        if not (dependency.get("engine") and any(reached.get(f) for f in _REACH_FIELDS)):
            names.update(str(tool) for tool in dependency.get("used_by") or [])
    return names


_TYPE_EXAMPLES = {"int": 1, "integer": 1, "number": 1, "float": 1.0, "bool": True, "boolean": True}


def _tool_request(tool) -> str:
    """One request for one tool, built from its contract: each argument takes the
    contract's first example value, else a plain value of its type."""
    values = {}
    for arg in tool.args:
        listed = tool.arg_values.get(arg)
        if isinstance(listed, list) and listed:
            values[arg] = listed[0]
        elif listed not in (None, [], ""):
            values[arg] = listed
        else:
            values[arg] = _TYPE_EXAMPLES.get(str(tool.arg_types.get(arg, "")).lower(), "test")
    shown = ", ".join(f"{arg}={value!r}" for arg, value in values.items())
    about = f" ({tool.description.strip()})" if tool.description.strip() else ""
    return (
        f"Please use your {tool.name} tool{about} now"
        + (f" with {shown}" if shown else "")
        + ". This runs in a disposable test world: call the tool once and tell me what it returns."
    )


# The per-tool requests after qualification: one fresh message per tool not yet shown,
# bounded so a check with many tools stays well inside the platform's check deadline.
TOOL_REQUEST_LIMIT = 8
TOOL_REQUEST_SECONDS = 300.0


async def request_uncovered_tools(
    *,
    interface,
    endpoint,
    runtime,
    contract,
    authoring: Path,
    bundle: Path,
    source: Path | None,
    secret_values,
    local_auth_values=None,
    timeout=120.0,
    budget=TOOL_REQUEST_SECONDS,
    limit=TOOL_REQUEST_LIMIT,
) -> None:
    """Ask the agent once for each tool the qualification conversation did not show.

    At most ``limit`` requests within ``budget`` seconds in all, so a check stays well
    inside its deadline; the tools left over are recorded as ``not_asked``.

    The world is disposable and reset after qualification, so a tool is asked whatever it
    changes there; only a tool the contract says leaves the world (no reachable
    implementation, or a dependency the environment cannot copy) is not asked, since the
    request could reach a real system. Each request is a fresh one-message conversation
    driven like a scenario's, so tool calls the agent returns are run at the world. Its
    routing is read from the world's side as in the conversation. Never raises.
    """
    from .call_runner import _collect_file_tool_calls
    from .chat_call_runner import _HostedChatTarget, _tool_world

    path = authoring / "generic-harness" / TOOL_ROUTING
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    body.setdefault("unobserved", [])
    shown = {one["tool"] for one in [*body["tools"], *body["unobserved"]]}
    outside = _outside_world(contract)
    logged = _logged_tools(contract, runtime, bundle)
    # Requests already sent by an earlier attempt of this check: their answers are reused
    # and their count and time spent stay within one bound for the whole check.
    ledger = _request_ledger(authoring)
    deadline = time.monotonic() + max(0.0, budget - ledger["seconds"])
    asked_count = len(ledger["asked"])
    for tool in getattr(contract, "tools", None) or []:
        if tool.name in shown:
            continue
        if tool.name in outside:
            # Asking could reach a real system: the contract says the tool leaves the world.
            body["unobserved"].append({"tool": tool.name, "reason": "reaches_outside"})
            continue
        if tool.name in ledger["asked"]:
            earlier = ledger["asked"][tool.name]
            body["tools"].extend(earlier["tools"])
            body["unobserved"].extend(earlier["unobserved"])
            continue
        if tool.name not in logged and getattr(tool, "effects", None):
            # The world keeps no record of this tool's calls, so asking could only change
            # something for no evidence.
            body["unobserved"].append({"tool": tool.name, "reason": "not_observable"})
            continue
        left = deadline - time.monotonic()
        if asked_count >= limit or left <= 0:
            body["unobserved"].append({"tool": tool.name, "reason": "not_asked"})
            continue
        asked_count += 1
        request_timeout = min(timeout, left)
        since = time.time()
        started = time.monotonic()
        try:
            traced = len(_collect_file_tool_calls(runtime))
            key = f"tool-request-{uuid4().hex}"
            target = _HostedChatTarget(
                wrapper=create_chat_wrapper(
                    interface,
                    endpoint,
                    timeout=request_timeout,
                    total_timeout=request_timeout,
                    secret_values=secret_values,
                    local_auth_values=local_auth_values,
                ),
                contract=contract,
                world=_tool_world(bundle, contract, runtime, source),
                scenario_key=key,
                scenario_id=key,
            )
            await target.say(_tool_request(tool))
            reported = [
                *target.world.calls,
                *_collect_file_tool_calls(runtime)[traced:],
            ]
        except Exception:  # noqa: BLE001 - an unanswered request leaves the tool unobserved
            failed = [{"tool": tool.name, "reason": "request_failed"}]
            body["unobserved"].extend(failed)
            _note_request(ledger, tool.name, [], failed, time.monotonic() - started)
            continue
        tools, unobserved = _routing(
            contract,
            runtime,
            received=[*world_call_log(runtime, since), *target.received],
            reported=[call for call in reported if call.name == tool.name],
            bundle=bundle,
        )
        asked = [
            {**one, "source": "tool_request"}
            for one in tools
            if one["tool"] == tool.name
        ]
        left_over = [one for one in unobserved if one["tool"] == tool.name]
        if not asked and not left_over:
            left_over = [{"tool": tool.name, "reason": "not_called"}]
        body["tools"].extend(asked)
        body["unobserved"].extend(left_over)
        _note_request(ledger, tool.name, asked, left_over, time.monotonic() - started)
    _write_routing(authoring, body)
    _save_ledger(authoring, ledger)


TOOL_REQUESTS = "tool-requests.json"


def _request_ledger(authoring: Path) -> dict:
    """The tool requests earlier attempts of this check sent, and the time they took."""
    path = authoring / "generic-harness" / TOOL_REQUESTS
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    asked = stored.get("asked") if isinstance(stored, dict) else None
    seconds = stored.get("seconds") if isinstance(stored, dict) else None
    return {
        "asked": asked if isinstance(asked, dict) else {},
        "seconds": float(seconds) if isinstance(seconds, (int, float)) else 0.0,
    }


def _note_request(
    ledger: dict, tool: str, tools: list, unobserved: list, spent: float
) -> None:
    ledger["asked"][tool] = {"tools": tools, "unobserved": unobserved}
    ledger["seconds"] += max(0.0, spent)


def _save_ledger(authoring: Path, ledger: dict) -> None:
    path = authoring / "generic-harness" / TOOL_REQUESTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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
        since = time.time()
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
        record_tool_routing(
            authoring,
            scenario.scenario_key,
            runner._contract,
            runtime,
            received=[*world_call_log(runtime, since), *runner.received],
            reported=outcome.calls,
            bundle=bundle,
        )
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
