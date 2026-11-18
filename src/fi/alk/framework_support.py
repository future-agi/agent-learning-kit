"""The frameworks the harness can take an agent from, and what is proven for each.

One source of truth: `docs/frameworks/supported.md` is checked against `markdown_table()`, and
`agent-learn capabilities` carries `as_records()` under `framework_support`. The keys are the
framework reader's own words (`scan_document.FRAMEWORK_NAMES`).

Three layers, each with one status and the evidence for it:

- **found**: the reader detects the framework in a repository;
- **read**: an agent built with it is read into the agent's contract, which the kit builds and binds;
- **run**: an agent read that way has run (the next owners' layer, kept separate).

Statuses: `proven`; `partial` (some of the layer is shown, the note says what is missing);
`pending` (proven on an open pull request, named in the evidence); `not_proven`.

Evidence is `test:<path>::<test>[<case>]`, `record:<path>#<heading>` or
`pr:future-agi/agent-learning-kit#<n> <path>::<test>`. A test checks that each one exists.

`tested_version` is set only where cited evidence ran that version of the package. An example's
pin is `example_pin`, which on its own proves nothing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

STATUSES = ("proven", "partial", "pending", "not_proven")
LAYERS = ("found", "read", "run")

_E2E = "record:docs/frameworks/evidence/2026-11-12-reference-readings.md"
_DETECT = "test:tests/harness/test_framework_detection.py"
_PR341 = "pr:future-agi/agent-learning-kit#341"


@dataclass(frozen=True)
class Layer:
    status: str
    evidence: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Framework:
    key: str
    name: str
    package: str
    tested_version: str | None
    tested_in: str
    example: str | None
    example_pin: str | None
    found: Layer
    read: Layer
    run: Layer


_NO_RUN_FROM_A_READING = "No run from a reading is recorded."

FRAMEWORKS: tuple[Framework, ...] = (
    Framework(
        key="claude_agent_sdk",
        name="Claude Agent SDK",
        package="claude-agent-sdk",
        tested_version="0.2.174",
        tested_in="sdk-smoke clean-install, the step that installs the example's pin",
        example="examples/harness/claude_agent_sdk_support",
        example_pin="0.2.174",
        found=Layer(
            "proven",
            (
                f"{_DETECT}::test_examples_report_their_framework[claude_agent_sdk_agent.py]",
                f"{_DETECT}::test_every_framework_is_detected[claude_code_sdk]",
                "test:tests/harness/test_claude_agent_sdk_reference.py"
                "::test_example_is_detected_as_the_claude_agent_sdk",
            ),
        ),
        read=Layer(
            "not_proven",
            (
                "test:tests/harness/test_claude_agent_sdk_reference.py"
                "::test_build_runs_the_callback_bridge_with_the_generic_tool_tracer",
            ),
            "No real reading is recorded. The kit builds and binds the example's tools from a "
            "hand-written contract.",
        ),
        run=Layer(
            "partial",
            (
                "test:tests/harness/test_claude_agent_sdk_reference.py"
                "::test_the_real_cli_runs_two_turns_and_only_the_plain_tools_are_traced",
            ),
            "The real SDK and its bundled CLI, against a local stub of the model API. "
            + _NO_RUN_FROM_A_READING,
        ),
    ),
    Framework(
        key="crewai",
        name="CrewAI",
        package="crewai",
        tested_version=None,
        tested_in="",
        example="examples/harness/crewai_support",
        example_pin="1.15.33",
        found=Layer(
            "proven",
            (
                f"{_DETECT}::test_examples_report_their_framework[crewai_agent.py]",
                "test:tests/harness/test_crewai_reference.py::test_example_is_detected_as_crewai",
            ),
        ),
        read=Layer(
            "not_proven",
            (
                "test:tests/harness/test_crewai_reference.py"
                "::test_build_runs_the_callback_bridge_with_the_generic_tool_tracer",
            ),
            "No real reading is recorded. The kit builds and binds the example's tools from a "
            "hand-written contract.",
        ),
        run=Layer(
            "not_proven",
            (),
            "Only against a stand-in CrewAI; the real package has not run in the kit's tests.",
        ),
    ),
    Framework(
        key="deepagents",
        name="Deep Agents",
        package="deepagents",
        tested_version="0.7.32",
        tested_in="the 12 Nov local runs (record)",
        example="examples/harness/deep_agents_support",
        example_pin="0.7.32",
        found=Layer(
            "proven",
            (
                f"{_DETECT}::test_every_framework_is_detected[deepagents]",
                "test:tests/harness/test_deep_agents_reference.py"
                "::test_example_is_detected_as_deep_agents",
            ),
        ),
        read=Layer(
            "pending",
            (
                f"{_E2E}#Deep Agents",
                f"{_PR341} tests/harness/test_reading_replay.py"
                "::test_the_deep_agents_reading_changes_as_designed",
                f"{_PR341} tests/harness/test_reading_routes.py"
                "::test_the_12_nov_deep_agents_reading_takes_the_graph_route",
            ),
            "The 12 Nov reading chose `langgraph dev`, which the agent doesn't install. #341 "
            "sends it to the kit's graph route; not merged.",
        ),
        run=Layer(
            "pending",
            (
                f"{_E2E}#Deep Agents",
                f"{_PR341} tests/harness/test_reading_routes.py"
                "::test_the_12_nov_deep_agents_reading_takes_the_graph_route",
            ),
            "From its reading it failed at start until #341. From a hand-written contract the "
            "kit's LangGraph adapter ran it with tool calls recorded.",
        ),
    ),
    Framework(
        key="google_adk",
        name="Google ADK",
        package="google-adk",
        tested_version="2.13.0",
        tested_in="the 12 Nov local runs (record)",
        example="examples/harness/google_adk_support",
        example_pin="2.13.0",
        found=Layer(
            "proven",
            (
                f"{_DETECT}::test_examples_report_their_framework[google_adk_agent.py]",
                "test:tests/harness/test_google_adk_reference.py"
                "::test_example_is_detected_as_an_adk_app",
            ),
        ),
        read=Layer(
            "proven",
            (
                f"{_E2E}#Google ADK",
                "test:tests/harness/test_google_adk_reference.py"
                "::test_build_runs_adks_own_server_and_binds_the_tool_tracer",
            ),
        ),
        run=Layer(
            "partial",
            (
                f"{_E2E}#Google ADK",
                "test:tests/harness/test_google_adk_reference.py"
                "::test_tools_run_by_adk_are_traced_once_each",
            ),
            "A local run from its reading gave right replies, but the transcript recorded two "
            "extra tool calls with empty arguments. No hosted run is recorded.",
        ),
    ),
    Framework(
        key="langchain",
        name="LangChain",
        package="langchain-core",
        tested_version=None,
        tested_in="",
        example=None,
        example_pin=None,
        found=Layer(
            "proven",
            (f"{_DETECT}::test_examples_report_their_framework[langchain_agent.py]",),
        ),
        read=Layer(
            "partial",
            (
                "test:tests/harness/test_agent_scan.py::test_a_langchain_chat_model_needs_no_ai_step",
            ),
            "Only the repository scan's entry is tested. No real reading is recorded.",
        ),
        run=Layer("not_proven", (), "No LangChain agent has run in the kit's tests."),
    ),
    Framework(
        key="langgraph",
        name="LangGraph",
        package="langgraph",
        tested_version=None,
        tested_in="",
        example=None,
        example_pin=None,
        found=Layer(
            "proven",
            (
                f"{_DETECT}::test_examples_report_their_framework[langgraph_agent.py]",
                f"{_DETECT}::test_every_framework_is_detected[langgraph_star]",
            ),
        ),
        read=Layer(
            "partial",
            (
                "test:tests/harness/test_agent_reading.py::test_the_framework_is_a_builds_shape[langgraph]",
            ),
            "Only the framework named in the agent document is tested. No real reading is "
            "recorded.",
        ),
        run=Layer(
            "not_proven",
            (),
            "Only the kit's LangGraph adapter against a stand-in graph. Deep Agents, built on "
            "LangGraph, is listed on its own.",
        ),
    ),
    Framework(
        key="livekit",
        name="LiveKit Agents",
        package="livekit-agents",
        tested_version=None,
        tested_in="",
        example=None,
        example_pin=None,
        found=Layer(
            "proven", (f"{_DETECT}::test_every_framework_is_detected[livekit]",)
        ),
        read=Layer(
            "not_proven", (), "No test reads a LiveKit agent into its contract."
        ),
        run=Layer(
            "partial",
            (
                "record:src/fi/alk/harness/IMPLEMENTATION_AND_VALIDATION_STATUS.md"
                "#Real voice-agent validation",
            ),
            "15 real calls on three livekit/agents examples (Aug 2026); the record doesn't give "
            "the livekit-agents version. The call engine's own tests import 1.8.3 but run no "
            "agent. " + _NO_RUN_FROM_A_READING,
        ),
    ),
    Framework(
        key="openai_agents",
        name="OpenAI Agents SDK",
        package="openai-agents",
        tested_version="0.24.0",
        tested_in="the 12 Nov local runs (record); the kit's hook also runs on 0.18.3 and 0.20.0 "
        "in sdk-smoke",
        example="examples/harness/openai_agents_support",
        example_pin="0.24.0",
        found=Layer(
            "proven",
            (
                f"{_DETECT}::test_examples_report_their_framework[openai_agents_agent.py]",
                "test:tests/harness/test_openai_agents_reference.py"
                "::test_example_is_detected_as_the_sdk_agent",
            ),
        ),
        read=Layer(
            "pending",
            (
                f"{_E2E}#OpenAI Agents SDK",
                f"{_PR341} tests/harness/test_reading_replay.py"
                "::test_the_agents_sdk_reading_changes_as_designed",
                f"{_PR341} tests/harness/test_reading_routes.py"
                "::test_the_12_nov_agents_sdk_reading_binds_its_three_tools",
            ),
            "The 12 Nov reading marked its tools unreachable. #341 binds them; not merged.",
        ),
        run=Layer(
            "pending",
            (
                f"{_E2E}#OpenAI Agents SDK",
                f"{_PR341} tests/harness/test_reading_routes.py"
                "::test_the_12_nov_agents_sdk_reading_binds_its_three_tools",
                "test:tests/harness/test_python_tool_trace_bootstrap.py"
                "::test_real_openai_agents_sdk_is_on_chat_completions_with_the_mark",
            ),
            "From its reading it gave right replies with no tool evidence until #341.",
        ),
    ),
    Framework(
        key="pipecat",
        name="Pipecat",
        package="pipecat-ai",
        tested_version=None,
        tested_in="",
        example=None,
        example_pin=None,
        found=Layer(
            "proven", (f"{_DETECT}::test_every_framework_is_detected[pipecat]",)
        ),
        read=Layer(
            "not_proven", (), "No test reads a Pipecat agent into its contract."
        ),
        run=Layer(
            "not_proven",
            (),
            "Only the kit's opt-in local Pipecat lane. Hosted Pipecat runs are TH-8871, on hold.",
        ),
    ),
    Framework(
        key="pydantic_ai",
        name="Pydantic AI",
        package="pydantic-ai-slim",
        tested_version="2.61.0",
        tested_in="the 12 Nov local runs (record)",
        example="examples/harness/pydantic_ai_support",
        example_pin="2.61.0",
        found=Layer(
            "proven",
            (
                f"{_DETECT}::test_examples_report_their_framework[pydantic_ai_agent.py]",
                "test:tests/harness/test_pydantic_ai_reference.py"
                "::test_example_is_detected_as_pydantic_ai",
            ),
        ),
        read=Layer(
            "proven",
            (
                f"{_E2E}#Pydantic AI",
                "test:tests/harness/test_pydantic_ai_reference.py"
                "::test_build_runs_the_callback_bridge_and_binds_the_tool_tracer",
            ),
        ),
        run=Layer(
            "partial",
            (f"{_E2E}#Pydantic AI",),
            "A local run from its reading: right replies and its tool calls recorded. No hosted "
            "run is recorded.",
        ),
    ),
)

_STATUS_WORDS = {
    "proven": "proven",
    "partial": "partly",
    "pending": "pending",
    "not_proven": "not proven",
}


def as_records() -> list[dict]:
    """The list as plain data, for `agent-learn capabilities` and the platform."""
    records = []
    for entry in FRAMEWORKS:
        record = asdict(entry)
        for layer in LAYERS:
            record[layer]["evidence"] = list(record[layer]["evidence"])
        records.append(record)
    return records


def markdown_table() -> str:
    """The table `docs/frameworks/supported.md` carries."""
    lines = [
        "| Framework | Package | Tested version | Found | Read | Run |",
        "|---|---|---|---|---|---|",
    ]
    for entry in FRAMEWORKS:
        words = [_STATUS_WORDS[getattr(entry, layer).status] for layer in LAYERS]
        version = entry.tested_version or "none"
        lines.append(
            f"| {entry.name} | `{entry.package}` | {version} | "
            + " | ".join(words)
            + " |"
        )
    return "\n".join(lines) + "\n"
