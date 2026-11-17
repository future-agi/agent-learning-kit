from __future__ import annotations

from pathlib import Path

import pytest

from fi.alk.harness.contract import AgentContract
from fi.alk.harness.tool_certification import (
    ToolAvailability,
    certify_tool_inventory,
)


def _contract(*, mode: str = "import") -> AgentContract:
    return AgentContract.model_validate(
        {
            "agent": "test",
            "real_use_cases": ["inspect status"],
            "tools": [{"name": "check_status", "args": ["id"]}],
            "tool_entrypoints": [
                {
                    "tool": "check_status",
                    "mode": mode,
                    "module": "agent",
                    "callable": "check_status",
                    "factory": "Agent()" if mode == "construct" else "",
                    "endpoint": "/status" if mode == "service" else "",
                }
            ],
        }
    )


def test_compiled_handler_is_certified_and_report_is_stable(tmp_path: Path) -> None:
    handlers = tmp_path / "handlers"
    handlers.mkdir()
    (handlers / "check_status.py").write_text("pass\n", encoding="utf-8")

    first = certify_tool_inventory(_contract(), tmp_path, external_provider=False)
    second = certify_tool_inventory(_contract(), tmp_path, external_provider=False)

    assert first == second
    assert first.certified_or_runtime_only == first.total == 1
    assert first.tools[0].availability is ToolAvailability.CERTIFIED


def test_missing_implementation_and_orphan_entrypoint_are_rejected(tmp_path: Path) -> None:
    payload = _contract().model_dump(mode="python")
    payload["tool_entrypoints"].append(
        {"tool": "invented", "mode": "service", "endpoint": "/x"}
    )
    contract = AgentContract.model_validate(payload)
    report = certify_tool_inventory(contract, tmp_path, external_provider=False)

    assert report.certified_or_runtime_only == 0
    assert {item.availability for item in report.tools} == {ToolAvailability.REJECTED}


def test_external_provider_tools_are_explicitly_runtime_only(tmp_path: Path) -> None:
    report = certify_tool_inventory(_contract(), tmp_path, external_provider=True)

    assert report.certified_or_runtime_only == report.total == 1
    assert report.tools[0].availability is ToolAvailability.RUNTIME_ONLY


def _voice(tools: list[str], entries: list[dict], *, modality: str = "voice"):
    """A nudge-rebuilt contract: the first build's entrypoints, tools added since without one."""
    return AgentContract.model_validate(
        {
            "agent": "rides",
            "modality": modality,
            "real_use_cases": ["book a ride"],
            "runtime": {"language": "python", "command": ["python", "agent.py"]},
            "tools": [{"name": name} for name in tools],
            "tool_entrypoints": entries,
        }
    )


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "agent").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "agent" / "ride_agent.py").write_text(
        "class RideAgent:\n"
        "    @function_tool()\n"
        "    async def search_places(self, text): ...\n"
        "    @function_tool(name='send_otp')\n"
        "    async def _send(self): ...\n",
        encoding="utf-8",
    )
    (source / "tests" / "test_rides.py").write_text(
        "def cancel_ride():\n    pass\n", encoding="utf-8"
    )
    return source


def test_a_voice_worker_runs_rebuilt_tools_its_source_defines(tmp_path: Path) -> None:
    contract = _voice(
        ["book_ride", "search_places", "send_otp"],
        [{"tool": "book_ride", "mode": "service", "endpoint": "book_ride"}],
    )

    report = certify_tool_inventory(
        contract, tmp_path, external_provider=False, source=_source(tmp_path)
    )

    assert report.certified_or_runtime_only == report.total == 3
    runtime = {item.tool: item.entrypoint_mode for item in report.tools}
    assert runtime == {
        "book_ride": "service",
        "search_places": "runtime",
        "send_otp": "runtime",
    }


def test_a_tool_the_runs_source_does_not_define_is_rejected_by_name(
    tmp_path: Path,
) -> None:
    contract = _voice(["search_places", "cancel_ride"], [])

    report = certify_tool_inventory(
        contract, tmp_path, external_provider=False, source=_source(tmp_path)
    )

    rejected = {
        item.tool: item.reason
        for item in report.tools
        if item.availability is ToolAvailability.REJECTED
    }
    assert rejected == {"cancel_ride": "the submitted source defines no cancel_ride"}


def test_tools_without_an_entrypoint_stay_rejected_outside_a_voice_worker(
    tmp_path: Path,
) -> None:
    chat = _voice(["search_places"], [], modality="chat")
    source = _source(tmp_path)

    for report in (
        certify_tool_inventory(chat, tmp_path, external_provider=False, source=source),
        certify_tool_inventory(
            _voice(["search_places"], []), tmp_path, external_provider=False
        ),
    ):
        assert report.tools[0].availability is ToolAvailability.REJECTED
        assert report.tools[0].entrypoint_mode == "missing"


def test_a_contract_with_every_entrypoint_certifies_the_same_with_its_source(
    tmp_path: Path,
) -> None:
    contract = _voice(
        ["book_ride"], [{"tool": "book_ride", "mode": "service", "endpoint": "book_ride"}]
    )

    assert certify_tool_inventory(
        contract, tmp_path, external_provider=False, source=_source(tmp_path)
    ) == certify_tool_inventory(contract, tmp_path, external_provider=False)


def test_named_service_without_per_tool_path_is_runtime_only(tmp_path: Path) -> None:
    payload = _contract(mode="service").model_dump(mode="python")
    payload["tool_entrypoints"][0]["endpoint"] = ""
    payload["tool_entrypoints"][0]["service"] = "remote-mcp"

    report = certify_tool_inventory(
        AgentContract.model_validate(payload), tmp_path, external_provider=False
    )

    assert report.certified_or_runtime_only == report.total == 1
    assert report.tools[0].availability is ToolAvailability.RUNTIME_ONLY


def test_native_tools_validate_declarations_without_claiming_execution(tmp_path):
    from fi.alk.harness.tool_certification import ToolProbeMode
    (tmp_path / "handlers").mkdir()
    (tmp_path / "handlers/check_status.py").write_text("raise AssertionError('must not execute')\n")
    for mode in ("import", "construct", "service"):
        report = certify_tool_inventory(
            _contract(mode=mode), tmp_path, external_provider=False,
            native_observation=True,
        )
        item, = report.tools
        assert item.availability is ToolAvailability.RUNTIME_ONLY
        assert item.probe_mode is ToolProbeMode.RUNTIME_ONLY
        assert item.schema_validated
        assert not item.implementation_validated
    payload = _contract().model_dump(mode="python")
    payload["tool_entrypoints"] = []
    report = certify_tool_inventory(
        AgentContract.model_validate(payload), tmp_path, external_provider=False,
        native_observation=True,
    )
    assert report.tools[0].availability is ToolAvailability.REJECTED


def test_native_incomplete_and_ambiguous_declarations_are_rejected(tmp_path):
    for mode, field in (("import", "module"), ("construct", "factory"), ("service", "endpoint")):
        payload = _contract(mode=mode).model_dump(mode="python")
        payload["tool_entrypoints"][0][field] = ""
        report = certify_tool_inventory(
            AgentContract.model_validate(payload), tmp_path, external_provider=False,
            native_observation=True,
        )
        assert report.tools[0].availability is ToolAvailability.REJECTED
    payload = _contract().model_dump(mode="python")
    payload["tool_entrypoints"] *= 2
    report = certify_tool_inventory(
        AgentContract.model_validate(payload), tmp_path, external_provider=False,
        native_observation=True,
    )
    assert report.tools[0].availability is ToolAvailability.REJECTED


# As a LangGraph async deep agents build authored them: the framework's own background-job tools, imported from the
# installed package, listed as the agent's tools beside two workers.
_ASYNC_JOB_TOOLS = (
    "launch_async_subagent",
    "check_async_subagent",
    "update_async_subagent",
    "cancel_async_subagent",
    "list_async_subagent_jobs",
)


def _async_deep_agents(*extra_tools: dict, extra_entries: tuple[dict, ...] = ()) -> AgentContract:
    return AgentContract.model_validate(
        {
            "agent": "supervisor",
            "real_use_cases": ["research and code in the background"],
            "tools": [
                *({"name": name, "args": ["description"], "tables_read": [], "tables_changed": []}
                  for name in _ASYNC_JOB_TOOLS),
                *extra_tools,
            ],
            "tool_entrypoints": [
                *({"tool": name, "mode": "import", "module": "deepagents", "callable": name}
                  for name in _ASYNC_JOB_TOOLS),
                *extra_entries,
            ],
            "workers": [{"id": "researcher", "name": "researcher"}, {"id": "coder", "name": "coder"}],
        }
    )


def _async_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "graphs" / "python" / "src").mkdir(parents=True)
    (source / "graphs" / "python" / "src" / "agent.py").write_text(
        "from deepagents import AsyncSubAgent\n", encoding="utf-8"
    )
    return source


def test_the_framework_s_own_worker_job_tools_need_no_world_code(tmp_path: Path) -> None:
    report = certify_tool_inventory(
        _async_deep_agents(), tmp_path, external_provider=False, source=_async_source(tmp_path)
    )

    assert {item.tool: item.availability for item in report.tools} == {
        name: ToolAvailability.RUNTIME_ONLY for name in _ASYNC_JOB_TOOLS
    }


def test_a_world_tool_whose_code_is_missing_is_still_rejected_beside_them(tmp_path: Path) -> None:
    source = _async_source(tmp_path)
    # The app's own tool, in its own module, with no compiled handler; and one from a library that writes a table.
    contract = _async_deep_agents(
        {"name": "save_report", "args": ["text"]},
        {"name": "store_row", "args": ["row"], "tables_changed": ["reports"]},
        extra_entries=(
            {"tool": "save_report", "mode": "import", "module": "src.agent", "callable": "save_report"},
            {"tool": "store_row", "mode": "import", "module": "somelib.sql", "callable": "store_row"},
        ),
    )
    (source / "graphs" / "python" / "src" / "__init__.py").write_text("", encoding="utf-8")

    report = certify_tool_inventory(contract, tmp_path, external_provider=False, source=source)

    by_tool = {item.tool: item.availability for item in report.tools}
    assert by_tool["save_report"] is ToolAvailability.REJECTED
    assert by_tool["store_row"] is ToolAvailability.REJECTED
    assert all(by_tool[name] is ToolAvailability.RUNTIME_ONLY for name in _ASYNC_JOB_TOOLS)


def test_job_tools_from_the_framework_s_own_library_in_its_monorepo_need_no_world_code(tmp_path: Path) -> None:
    """The deep agents repository as a live build met it: the `deepagents` library ships in the same repository as the
    async example that depends on it. Its job tools are still the framework's, not the app's world tools."""
    source = _async_source(tmp_path)
    library = source / "libs" / "deepagents"
    (library / "deepagents").mkdir(parents=True)
    (library / "deepagents" / "__init__.py").write_text("", encoding="utf-8")
    (library / "pyproject.toml").write_text("[project]\nname='deepagents'\nversion='1'\n", encoding="utf-8")
    (source / "graphs" / "python" / "pyproject.toml").write_text(
        "[project]\nname='async-subagents'\nversion='1'\ndependencies=['deepagents>=0.1', 'langgraph']\n",
        encoding="utf-8",
    )

    report = certify_tool_inventory(_async_deep_agents(), tmp_path, external_provider=False, source=source)

    assert {item.tool: item.availability for item in report.tools} == {
        name: ToolAvailability.RUNTIME_ONLY for name in _ASYNC_JOB_TOOLS
    }


def test_the_app_s_own_package_is_never_read_as_the_framework_s(tmp_path: Path) -> None:
    """A package the source holds as the app itself (no other project depends on it) keeps its tools world tools."""
    source = _async_source(tmp_path)
    app = source / "travel_concierge"
    (app / "deepagents").mkdir(parents=True)
    (app / "deepagents" / "__init__.py").write_text("", encoding="utf-8")
    (source / "pyproject.toml").write_text("[project]\nname='deepagents'\nversion='1'\n", encoding="utf-8")

    report = certify_tool_inventory(_async_deep_agents(), tmp_path, external_provider=False, source=source)

    assert all(item.availability is not ToolAvailability.RUNTIME_ONLY for item in report.tools)


def test_job_tools_a_framework_middleware_injects_are_the_framework_s_even_with_no_importable_seam(tmp_path: Path) -> None:
    """As the live deep agents build recorded them: `unreachable`, injected by deepagents.AsyncSubAgentMiddleware while
    the agent runs. Defined nowhere in the app's source, they are the framework's, not world tools to certify; the
    app's own tools beside them keep their checks."""
    source = _async_source(tmp_path)
    (source / "graphs" / "python" / "src" / "researcher.py").write_text(
        "def analyze_topic(topic):\n    return topic\n", encoding="utf-8"
    )
    contract = AgentContract.model_validate(
        {
            "agent": "supervisor",
            "real_use_cases": ["research and code in the background"],
            "tools": [
                *({"name": name, "args": ["description"]} for name in _ASYNC_JOB_TOOLS),
                {"name": "analyze_topic", "args": ["topic"]},
            ],
            "tool_entrypoints": [
                *(
                    {"tool": name, "mode": "unreachable", "callable": name,
                     "notes": "Injected dynamically by deepagents.AsyncSubAgentMiddleware"}
                    for name in _ASYNC_JOB_TOOLS
                ),
                {"tool": "analyze_topic", "mode": "unreachable", "callable": "analyze_topic"},
            ],
            "workers": [{"id": "researcher", "name": "researcher"}, {"id": "coder", "name": "coder"}],
        }
    )

    report = certify_tool_inventory(contract, tmp_path, external_provider=False, source=source)

    by_tool = {item.tool: item.availability for item in report.tools}
    assert all(by_tool[name] is ToolAvailability.RUNTIME_ONLY for name in _ASYNC_JOB_TOOLS)
    # No known framework injects this one: no seam is still a rejection, as before.
    assert by_tool["analyze_topic"] is ToolAvailability.REJECTED
    # Without workers it is no worker coordination: rejected, as before.
    alone = certify_tool_inventory(
        contract.model_copy(update={"workers": []}), tmp_path, external_provider=False, source=source
    )
    assert all(item.availability is ToolAvailability.REJECTED for item in alone.tools)


@pytest.mark.parametrize(
    ("tool", "notes", "uses_framework"),
    [
        # A factory-made customer tool (Tool(name="refund"), StructuredTool.from_function): same shape, no injector.
        ("refund", "built with StructuredTool.from_function", True),
        ("issue_refund", "registered through an MCP server", False),
        # A framework's tool name in an app that never uses that framework, with nothing saying it injects it.
        ("launch_async_subagent", "", False),
    ],
)
def test_a_tool_with_no_seam_that_no_known_framework_injects_stays_rejected(
    tmp_path: Path, tool: str, notes: str, uses_framework: bool
) -> None:
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "app" / "agent.py").write_text(
        ("from deepagents import AsyncSubAgent\n" if uses_framework else "")
        + "from langchain_core.tools import StructuredTool\n",
        encoding="utf-8",
    )
    contract = AgentContract.model_validate(
        {
            "agent": "returns",
            "real_use_cases": ["refunds"],
            "tools": [{"name": tool, "args": ["order_id"]}],
            "tool_entrypoints": [{"tool": tool, "mode": "unreachable", "notes": notes}],
            "workers": [{"id": "refund", "name": "refund"}],
        }
    )

    report = certify_tool_inventory(contract, tmp_path, external_provider=False, source=source)

    assert [item.availability for item in report.tools] == [ToolAvailability.REJECTED]


def test_the_build_check_lets_the_framework_s_injected_job_tools_through(tmp_path: Path) -> None:
    from fi.alk.harness.build import blockers

    source = _async_source(tmp_path)
    contract = AgentContract.model_validate(
        {
            "agent": "supervisor",
            "real_use_cases": ["research in the background"],
            "tools": [{"name": "launch_async_subagent", "args": ["description"]}],
            "tool_entrypoints": [{"tool": "launch_async_subagent", "mode": "unreachable"}],
            "workers": [{"id": "researcher", "name": "researcher"}],
        }
    )
    assert not [one for one in blockers(contract, str(source)) if "launch_async_subagent" in one]
    # A factory-made tool of the same shape still blocks the build.
    factory = contract.model_copy(
        update={
            "tools": [*contract.tools, *AgentContract.model_validate({"tools": [{"name": "refund"}]}).tools],
            "tool_entrypoints": [
                *contract.tool_entrypoints,
                *AgentContract.model_validate({"tool_entrypoints": [{"tool": "refund", "mode": "unreachable"}]}).tool_entrypoints,
            ],
        }
    )
    blocked = blockers(factory, str(source))
    assert [one for one in blocked if one.startswith("refund")]
    assert not [one for one in blocked if "launch_async_subagent" in one]
