import pytest
from pydantic import ValidationError

from fi.alk.harness.contract import (
    AgentContract,
    RuntimeInterface,
    ToolEntry,
    ToolSpec,
    validate_contract,
)


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


def _credential_contract(arg_values=None, endpoint=""):
    return AgentContract(
        agent="support",
        real_use_cases=["log in"],
        tools=[ToolSpec(name="login", args=["password", "q"], arg_values=arg_values or {})],
        tool_entrypoints=[
            ToolEntry(tool="login", mode="service", service="api", endpoint=endpoint)
        ],
    )


def _credential_problems(contract):
    return [problem for problem in validate_contract(contract) if "holds a credential" in problem]


@pytest.mark.parametrize(
    "endpoint",
    [
        "/v1/search?key=AbCdEf123456",
        "/c?sv=2020&sig=abcDEF123%2F",
        "/v1/items?pwd=hunter2",
        "/login?auth=hunter2",
    ],
)
def test_an_endpoint_with_a_credential_in_its_query_is_a_problem(endpoint):
    problems = _credential_problems(_credential_contract(endpoint=endpoint))
    assert len(problems) == 1
    assert "tool_entrypoints[login].endpoint holds a credential" in problems[0]
    assert "through its settings" in problems[0]


@pytest.mark.parametrize(
    "endpoint",
    [
        "/v1/items?id=77",
        "https://x.example.com/v1?api-version=2024-01-01",
        "/docs#install",
        "HTTPS://API.EXAMPLE.COM/v1",
        "{HOST}:{PORT}/v1",
        "ssh://git@github.com/org/repo",
    ],
)
def test_an_honest_endpoint_is_not_a_credential_problem(endpoint):
    assert _credential_problems(_credential_contract(endpoint=endpoint)) == []


def test_a_credential_shaped_example_value_is_a_problem():
    problems = _credential_problems(
        _credential_contract(arg_values={"q": ["/x?sig=abcDEF123%2F"]})
    )
    assert len(problems) == 1
    assert "tools[login].arg_values.q holds a credential" in problems[0]


def test_a_credential_named_argument_may_not_list_values():
    problems = _credential_problems(
        _credential_contract(arg_values={"password": ["hunter2", "correcthorse"]})
    )
    assert len(problems) == 1
    assert "tools[login].arg_values.password holds a credential" in problems[0]
    assert "hunter2" not in problems[0]


def test_a_credential_named_argument_may_list_references():
    contract = _credential_contract(arg_values={"password": ["$PASSWORD", "{{pw}}"]})
    assert _credential_problems(contract) == []


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


# `write_contract` keeps these fields as written, so a credential in one would be saved and sent on.
_URL_WITH_PASSWORD = "https://deploy:s3cretPass@pypi.internal.example.com/simple"
_GITHUB_TOKEN = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5"


def _kept_field_contract(**fields):
    from fi.alk.harness.contract import AgentContract

    return AgentContract(agent="support", real_use_cases=["log in"], **fields)


_KEPT_FIELDS = {
    "runtime.install": (
        lambda: {
            "runtime": {
                "language": "python",
                "install": f"pip install --index-url {_URL_WITH_PASSWORD} -r requirements.txt",
            }
        },
        _URL_WITH_PASSWORD,
    ),
    "runtime.workspaces": (
        lambda: {"runtime": {"language": "python", "workspaces": {"work": _GITHUB_TOKEN}}},
        _GITHUB_TOKEN,
    ),
    "runtime.compose_overrides": (
        lambda: {
            "runtime": {
                "execution": "compose",
                "compose_file": "compose.yaml",
                "service": "app",
                "compose_overrides": [f"overrides/{_GITHUB_TOKEN}.yaml"],
            }
        },
        _GITHUB_TOKEN,
    ),
    "dependencies[db].reached": (
        lambda: {
            "dependencies": [
                {"name": "db", "reached": {"dsn_env": f"postgres://svc:{'Zq9fakePw41'}@db.internal/app"}}
            ]
        },
        "Zq9fakePw41",
    ),
    "data_store": (
        lambda: {"data_store": {"kind": "postgres", "configured_by": _URL_WITH_PASSWORD}},
        _URL_WITH_PASSWORD,
    ),
}


@pytest.mark.parametrize("where", sorted(_KEPT_FIELDS))
def test_a_credential_in_a_kept_runtime_dependency_or_data_store_field_is_a_problem(where):
    build, secret = _KEPT_FIELDS[where]
    contract = _kept_field_contract(**build())
    problems = _credential_problems(contract)
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"{where} holds a credential")
    assert secret not in problems[0]
    assert "placeholder" in problems[0]


def test_honest_runtime_dependency_and_data_store_fields_are_not_credential_problems():
    contract = _kept_field_contract(
        runtime={
            "language": "python",
            "install": "pip install -r requirements.txt",
            "workspaces": {"work": "workspace"},
        },
        dependencies=[{"name": "db", "reached": {"dsn_env": "DATABASE_URL", "host": "db.internal", "port": 5432}}],
        data_store={"kind": "postgres", "configured_by": "DATABASE_URL", "host": "db.internal"},
    )
    assert _credential_problems(contract) == []
