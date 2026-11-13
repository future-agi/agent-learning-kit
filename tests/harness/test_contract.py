import pytest
from pydantic import ValidationError

from fi.alk.harness.contract import AgentContract, RuntimeInterface, validate_contract


def test_response_path_schema_describes_supported_selector_in_authoring_and_repair(tmp_path):
    from fi.alk.harness.runtime_repair import _RuntimeSubmission
    from fi.alk.harness.tools import contract_tools
    from fi.simulate.agent.wrappers.http import _select_response_value

    description = RuntimeInterface.model_json_schema()["properties"]["response_path"]["description"]
    submit = next(tool for tool in contract_tools(tmp_path).tools if tool.name == "submit_contract")
    initial = submit.input_schema["properties"]["runtime"]["properties"]["interface"]["properties"]
    repair = _RuntimeSubmission.model_json_schema()["$defs"]["RuntimeInterface"]["properties"]
    assert initial["response_path"]["description"] == repair["response_path"]["description"] == description
    assert "dot-separated" in description
    assert "Negative indexes" in description
    assert "Bracket indexing and JSONPath expressions are not supported" in description
    assert "output.-1.content.0.text" in description
    payload = {"output": [{"content": [{"text": "earlier"}]}, {"content": [{"text": "answer"}]}]}
    assert _select_response_value(payload, "output.-1.content.0.text") == "answer"
    with pytest.raises(ValueError, match="response_path segment not found"):
        _select_response_value(payload, "output[-1].content[0].text")


def test_generic_http_contract_rejects_unbound_or_forward_template_variables():
    with pytest.raises(
        ValidationError, match="runtime_http_setup_unknown_placeholders"
    ):
        RuntimeInterface.model_validate(
            {
                "kind": "http",
                "port": 8080,
                "path": "/run",
                "protocol": "json_template",
                "setup_requests": [
                    {
                        "path": "/users/{{user_id}}/sessions",
                        "capture": {"session_id": "id"},
                    }
                ],
                "request_template": {"session_id": "{{session_id}}"},
            }
        )
    with pytest.raises(
        ValidationError, match="runtime_http_request_unknown_placeholders"
    ):
        RuntimeInterface.model_validate(
            {
                "kind": "http",
                "port": 8080,
                "path": "/run",
                "protocol": "json_template",
                "request_template": {"session_id": "{{session_id}}"},
            }
        )
    assert RuntimeInterface.model_validate(
        {
            "kind": "http",
            "port": 8080,
            "path": "/run",
            "protocol": "json_template",
            "setup_requests": [
                {
                    "path": "/users/{{thread_id}}/sessions",
                    "capture": {"session_id": "id"},
                }
            ],
            "request_template": {"session_id": "{{session_id}}"},
        }
    ).setup_requests[0].capture == {"session_id": "id"}


def test_an_import_entrypoint_without_module_and_callable_is_rejected_at_contract_time():
    """Bundling compiles a binding from exactly these two fields, so a half-filled entry is dead.

    Observed on a dev run: the model recorded `lookup_policy` as `construct` with no module or
    callable, the contract was accepted, and the job died three runtime-validation attempts later
    with `contract_tool_entry_incomplete`. Catching it here tells the model while it can still fix
    the entry.
    """
    contract = AgentContract.model_validate(
        {
            "agent": "hotel",
            "real_use_cases": ["book a room"],
            "tools": [{"name": "lookup_policy", "args": []}],
            "tool_entrypoints": [{"tool": "lookup_policy", "mode": "construct"}],
        }
    )
    problems = validate_contract(contract)
    assert any(
        "lookup_policy" in p and "needs-module-and-callable" in p for p in problems
    ), problems


def test_a_complete_entrypoint_and_an_unreachable_one_both_pass():
    contract = AgentContract.model_validate(
        {
            "agent": "hotel",
            "real_use_cases": ["book a room"],
            "tools": [{"name": "a", "args": []}, {"name": "b", "args": []}],
            "tool_entrypoints": [
                {
                    "tool": "a",
                    "mode": "construct",
                    "module": "pkg.mod",
                    "callable": "Klass.method",
                },
                {"tool": "b", "mode": "unreachable"},
            ],
        }
    )
    assert not [
        p for p in validate_contract(contract) if "needs-module-and-callable" in p
    ]


def test_failure_event_schema_is_available_in_authoring_and_repair(tmp_path):
    from fi.alk.harness.runtime_repair import _RuntimeSubmission
    from fi.alk.harness.tools import contract_tools
    from fi.simulate.agent.wrappers.http_events import CompletedToolFailureEvent

    submit = next(tool for tool in contract_tools(tmp_path).tools if tool.name == "submit_contract")
    initial = submit.input_schema["properties"]["runtime"]["properties"]["interface"]["properties"]["completed_tool_events"]
    repair = _RuntimeSubmission.model_json_schema()
    field = initial["properties"]["failure_events"]
    assert field["maxItems"] == 8
    assert field["items"] == CompletedToolFailureEvent.model_json_schema()
    assert "$defs" not in initial
    assert repair["$defs"]["CompletedToolEvents"]["properties"]["failure_events"]["items"] == {
        "$ref": "#/$defs/CompletedToolFailureEvent",
    }
    assert repair["$defs"]["CompletedToolFailureEvent"] == field["items"]
    assert field["items"]["required"] == ["kind", "action_id_path", "result_path"]


@pytest.mark.parametrize("argument", [
    "{{new_message}}",
    "{{new_message_content}}",
    "{{messages}}",
    "{{ new_message }}",
    "--query={{new_message_content}}",
])
def test_runtime_command_rejects_unexpanded_message_templates(argument):
    from fi.alk.harness.contract import Runtime

    with pytest.raises(ValueError, match="runtime_command_message_template_unsupported"):
        Runtime.model_validate({
            "execution": "native",
            "command": ["agent", "chat", "--query", argument],
            "interface": {"kind": "command", "protocol": "fi.alk"},
        })


@pytest.mark.parametrize("argument", [
    '{"message":"hello", "options":{}}',
    "{message}",
    "{{source_template}}",
    "{{PORT_agent}}",
    "{1..4}",
])
def test_runtime_command_preserves_static_braces(argument):
    from fi.alk.harness.contract import Runtime

    command = ["agent", "--format", argument]
    assert Runtime(command=command, interface={"kind": "command", "protocol": "fi.alk"}).command == command


def test_runtime_command_rejects_message_template_in_normalized_shell_string():
    from fi.alk.harness.contract import Runtime

    with pytest.raises(ValueError, match="runtime_command_message_template_unsupported"):
        Runtime(command="agent chat -q '{{new_message}}'", interface={"kind": "command", "protocol": "fi.alk"})


def test_http_server_keeps_source_owned_message_template_argument():
    from fi.alk.harness.contract import Runtime

    command = ["server", "--chat-template", "{{messages}}"]
    runtime = Runtime(command=command, interface={"kind": "http", "protocol": "openai_chat", "port": 8080, "path": "/v1/chat/completions"})
    assert runtime.command == command


def test_declared_workers_name_contract_tools_and_reach_the_brief():
    contract = AgentContract.model_validate(
        {
            "agent": "returns",
            "real_use_cases": ["refund an order"],
            "tools": [{"name": "get_order"}, {"name": "issue_refund"}],
            "workers": [
                {
                    "id": "order_lookup",
                    "name": "Order-lookup worker",
                    "tools": ["get_order"],
                },
                {"id": "refund", "name": "Refund worker", "tools": ["issue_refund"]},
            ],
        }
    )
    assert validate_contract(contract) == []
    assert "order_lookup (Order-lookup worker): get_order" in contract.brief()


def test_separate_workers_sharing_an_address_or_with_an_id_no_channel_can_carry_are_returned_to_authoring():
    contract = AgentContract.model_validate(
        {
            "agent": "returns",
            "real_use_cases": ["refund an order"],
            "tools": [{"name": "get_order"}],
            "workers": [
                {"id": "refund", "binding": "separate", "address_env": "TOOLS_URL"},
                {"id": "order_lookup", "binding": "separate", "address_env": "TOOLS_URL"},
                {"id": "Order lookup", "binding": "separate", "address_env": "LOOKUP_URL"},
                {"id": "audit", "binding": "separate"},
                {"id": "Shared one", "binding": "shared", "address_env": "TOOLS_URL"},
            ],
        }
    )
    problems = validate_contract(contract)
    assert [one.split(":")[0] + ":" + one.split(":")[1] for one in problems] == [
        "worker[2]:id-not-usable-for-a-channel",
        "worker[3]:separate-without-address-env",
        "duplicate-worker-address-env:TOOLS_URL",
    ]


def test_a_worker_without_an_id_of_its_own_is_returned_to_authoring_and_keeps_only_contract_tools():
    contract = AgentContract.model_validate(
        {
            "agent": "returns",
            "real_use_cases": ["refund an order"],
            "tools": [{"name": "get_order"}],
            "workers": [
                {"id": "order_lookup", "tools": ["get_order", "send_email"]},
                {"id": "order_lookup", "name": "Second"},
                {"id": " "},
            ],
        }
    )
    problems = validate_contract(contract)
    assert [one.split(":")[0] for one in problems] == [
        "worker[2]",
        "duplicate-worker-ids",
    ]
    assert "duplicate-worker-ids:order_lookup:" in problems[1]
    assert [(one.id, one.tools) for one in contract.workers] == [
        ("order_lookup", ["get_order"]),
        ("order_lookup", []),
        (" ", []),
    ]
    fixed = contract.model_copy(
        update={
            "workers": [
                contract.workers[0],
                contract.workers[1].model_copy(update={"id": "refunds"}),
            ]
        }
    )
    assert validate_contract(fixed) == []


def test_contract_without_workers_is_unchanged():
    contract = AgentContract.model_validate(
        {"agent": "plain", "real_use_cases": ["answer"], "tools": [{"name": "a"}]}
    )
    assert contract.workers == []
    assert "WORKERS" not in contract.brief()


def test_a_sub_goal_names_the_task_or_one_worker_and_a_task_subject_is_saved_as_before(tmp_path):
    import json

    from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue

    assert SubGoal(name="a").subject == "task" and SubGoal(name="a").worker == ""
    assert SubGoal(name="b", subject="worker:refund").worker == "refund"
    for bad in ("refund", "worker:", "agent:x"):
        with pytest.raises(ValidationError, match="subject is task or worker"):
            SubGoal(name="c", subject=bad)
    path = save_catalogue(Catalogue(sub_goals=[SubGoal(name="a"), SubGoal(name="b", subject="worker:refund")]), tmp_path)
    saved = json.loads(path.read_text())["sub_goals"]
    assert "subject" not in saved[0] and saved[1]["subject"] == "worker:refund"


def test_permitted_tools_on_a_shared_worker_are_returned_to_authoring():
    contract = AgentContract.model_validate(
        {
            "agent": "returns",
            "real_use_cases": ["refund an order"],
            "tools": [{"name": "get_order"}, {"name": "issue_refund"}],
            "workers": [
                {"id": "lookup", "tools": ["get_order"], "allowed_tools": ["get_order"], "binding": "shared"},
                {
                    "id": "refund",
                    "tools": ["get_order", "issue_refund"],
                    "allowed_tools": ["issue_refund"],
                    "binding": "separate",
                    "address_env": "REFUND_URL",
                },
            ],
        }
    )
    assert [one.split(": ")[0] for one in validate_contract(contract)] == [
        "worker[0]:allowed-tools-on-a-shared-worker"
    ]


def test_a_worker_allowed_a_tool_it_does_not_hold_is_returned_to_authoring():
    contract = AgentContract.model_validate(
        {
            "agent": "returns",
            "real_use_cases": ["refund an order"],
            "tools": [{"name": "get_order"}, {"name": "issue_refund"}],
            "workers": [
                {
                    "id": "order_lookup",
                    "tools": ["get_order"],
                    "allowed_tools": ["get_order"],
                    "binding": "separate",
                    "address_env": "LOOKUP_URL",
                },
                {
                    "id": "refund",
                    "tools": ["issue_refund"],
                    "allowed_tools": ["get_order"],
                    "binding": "separate",
                    "address_env": "REFUND_URL",
                },
            ],
        }
    )
    assert [one.split(": ")[0] for one in validate_contract(contract)] == [
        "worker[1]:allowed-tools-it-does-not-hold:get_order"
    ]


def test_a_scenario_s_worker_budget_is_positive_and_absent_from_its_document_when_unset():
    from fi.alk.harness.folder import document_for
    from fi.alk.harness.scenario import Scenario

    plain = Scenario(name="a", instruction="x")
    assert "worker_budget_seconds" not in document_for(plain)
    assert document_for(plain.model_copy(update={"worker_budget_seconds": 20}))["worker_budget_seconds"] == 20
    with pytest.raises(ValidationError):
        Scenario(name="a", instruction="x", worker_budget_seconds=0)
    # One worker can be given its own budget, so one worker times out while the others finish.
    per_worker = Scenario(name="a", instruction="x", worker_budget_seconds={"refund": 2})
    assert document_for(per_worker)["worker_budget_seconds"] == {"refund": 2}
    for bad in ({"refund": 0}, {"": 2}, {}):
        with pytest.raises(ValidationError):
            Scenario(name="a", instruction="x", worker_budget_seconds=bad)


# The platform's result receipt validates a sub-goal's subject with this pattern
# (future-agi simulate/serializers/hosted_harness.py). Every subject the harness accepts must match it, or the whole
# receipt is refused and the call's result is lost.
PLATFORM_SUBJECT = r"^(task|worker:.*\S.*)$"


@pytest.mark.parametrize("subject", ["task", "worker:refund", "worker:Order lookup", "worker:order-lookup_2"])
def test_every_subject_the_harness_accepts_matches_the_platform_s_receipt_rule(subject):
    import re

    from fi.alk.harness.catalogue import SubGoal

    accepted = SubGoal.model_validate({"name": "g", "subject": subject}).subject
    assert re.match(PLATFORM_SUBJECT, accepted)


@pytest.mark.parametrize("subject", ["worker:", "worker:   ", "agent"])
def test_a_subject_the_platform_would_refuse_is_refused_by_the_harness_too(subject):
    import re

    from fi.alk.harness.catalogue import SubGoal

    assert not re.match(PLATFORM_SUBJECT, subject.strip())
    with pytest.raises(ValueError):
        SubGoal.model_validate({"name": "g", "subject": subject})
