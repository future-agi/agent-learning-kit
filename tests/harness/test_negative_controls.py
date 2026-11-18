"""Task-specific negative controls must fail the checks, and a refusal case is proved by them."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest
from proof_world import (
    BASE,
    LOOKUP_O10,
    REFUND_CATALOGUE,
    REFUND_O10,
    catalogue,
    check,
    control,
    install,
    refund_case,
    scenario,
)

from fi.alk.harness import live_proof
from fi.alk.harness import prove as module
from fi.alk.harness.contract import ToolEntry
from fi.alk.harness.prove import Proof
from fi.alk.harness.world.runtime import Call

LOOKUP_O20 = {"tool": "lookup_order", "arguments": {"order_id": "O-20"}, "fixed": {"order_id": "the caller"}}
REFUND_O20 = {"tool": "refund_order", "arguments": {"order_id": "O-20"}, "fixed": {"order_id": "the caller"}}
NO_REFUND = check(
    "if world.data['refunds'] or any(c.name == 'refund_order' for c in calls):\n    return 'a refund was attempted'\nreturn None"
)
LOOKED_UP_O20 = check(
    "return None if any(c.name == 'lookup_order' and c.ok and c.arguments.get('order_id') == 'O-20' for c in calls) "
    "else 'O-20 never looked up'"
)


def _refusal(sub_goals=("no_refund", "looked_up_o20")):
    return scenario(
        [LOOKUP_O20],
        name="refuse_20_day",
        sub_goals=list(sub_goals),
        instruction="I bought this 20 days ago and want my money back.",
        negative_controls=[control("refund_anyway", "refunds the 20-day order", [LOOKUP_O20, REFUND_O20])],
    )


def test_two_refunds_control_fails(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(refund_case(), catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.controls[0]["name"] == "two_refunds"
    assert proof.controls[0]["failed"] is True
    assert proof.proved is True and proof.blocked_reason() is None


def test_control_that_passes_blocks(monkeypatch, tmp_path):
    install(monkeypatch)
    loose = check("return None if len(world.data['refunds']) >= 1 else 'no refund'")
    proof = module.prove(refund_case(), catalogue(refund_once=loose, looked_up=REFUND_CATALOGUE["looked_up"]), tmp_path)
    assert proof.blocked_reason() == "negative_control_passed"
    assert "refund_once" in proof.controls[0]["held"]
    assert proof.proved is False


def test_no_control_blocks(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(refund_case(negative_controls=[]), catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.blocked_reason() == "no_negative_control"
    assert proof.proved is False and proof.kept_as_written is False


def test_refusal_case_outcome_no_change(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.outcome == "no_change"
    assert proof.controls[0]["name"] == "refund_anyway" and proof.controls[0]["failed"] is True


def test_no_change_case_holds_despite_weak_checks(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.weak == ["no_refund"]
    assert proof.holds is True and proof.proved is True


def test_control_with_unbound_tool_is_not_run(monkeypatch, tmp_path):
    install(monkeypatch, runtime_tools={"place_order"})
    place = {"tool": "place_order", "arguments": {"order_id": "O-10"}}
    case = refund_case(negative_controls=[control("places_instead", "places a new order", [LOOKUP_O10, REFUND_O10, place])])
    proof = module.prove(case, catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.controls[0]["assumed"] == ["place_order"]
    assert proof.blocked_reason() == "control_not_run"
    assert proof.proved is False and proof.kept_as_written is True


def test_refusal_lookup_without_check_is_unchecked(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(sub_goals=("no_refund",)), catalogue(no_refund=NO_REFUND), tmp_path)
    assert proof.unchecked == ["lookup_order.order_id"]
    assert proof.blocked_reason() == "unchecked"


def test_unreadable_state_reads_change(monkeypatch, tmp_path):
    install(monkeypatch, unreadable=True)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.outcome == "change"


def test_assumed_write_never_reads_no_change(monkeypatch, tmp_path):
    # The refund step never ran, so the unchanged world says nothing: the case is no refusal case.
    install(monkeypatch, runtime_tools={"refund_order"})
    proof = module.prove(refund_case(), catalogue(**REFUND_CATALOGUE), tmp_path, contract=None, actors=[])
    assert proof.assumed == ["refund_order"]
    assert proof.outcome == "change"


class ServiceWorld:
    """The live world over one shared datastore: a bound tool is a POST to the tools service."""

    def __init__(self, store: dict) -> None:
        self.store = store
        self.calls: list[Call] = []
        self.runtime_tools: set[str] = set()
        self.endpoint_for: dict[str, str] = {}
        self.forwarded: list[str] = []

    @property
    def data(self) -> dict:
        return self.store

    def bind_tools(self, proxy, endpoint_for, runtime_tools, *, mcp_toolbox="") -> None:
        assert proxy == "http://proxy"
        self.endpoint_for = dict(endpoint_for)
        self.runtime_tools = set(runtime_tools) | set(endpoint_for)

    def forward(self, endpoint, arguments=None, *, record_as="", record=True) -> Call:
        self.forwarded.append(endpoint)
        args = dict(arguments or {})
        order = self.store["orders"].get(args.get("order_id"))
        if endpoint == "lookup":
            return Call(endpoint, args, result=order, ok=order is not None)
        if order is None or order["age_days"] > 14:
            return Call(endpoint, args, ok=False, refused=True, error="refused by policy")
        self.store["refunds"].append(args["order_id"])
        return Call(endpoint, args, result={"refunded": args["order_id"]})

    def call(self, name, arguments=None) -> Call:
        call = self.forward(self.endpoint_for[name], arguments)
        self.calls.append(call)
        return call

    def state(self) -> dict:
        return copy.deepcopy(self.store)


class Stack:
    """A runtime whose reset restores the seeded datastore, and a factory over it."""

    def __init__(self) -> None:
        self.store = copy.deepcopy(BASE)
        self.resets = 0
        self.endpoints = {"tool_proxy": SimpleNamespace(address="http://proxy"), "target_http": "x"}
        self.worlds: list[ServiceWorld] = []

    async def reset(self, runtime, *, work_directory) -> None:
        self.resets += 1
        self.store.clear()
        self.store.update(copy.deepcopy(BASE))

    async def create(self, runtime, *, rng) -> ServiceWorld:
        world = ServiceWorld(self.store)
        self.worlds.append(world)
        return world


def _contract(modes: dict[str, str]):
    entries = {name: ToolEntry(tool=name, mode=mode, endpoint={"lookup_order": "lookup", "refund_order": "refund"}[name]) for name, mode in modes.items()}
    return SimpleNamespace(tool_names=lambda: list(modes), entry_for=entries.get)


def _prove(monkeypatch, tmp_path, modes, goals=REFUND_CATALOGUE, **options):
    monkeypatch.setattr(live_proof, "load_scenarios", lambda path: [refund_case()])
    monkeypatch.setattr(live_proof, "load_catalogue", lambda path: catalogue(**goals))
    monkeypatch.setattr(live_proof, "_digest", lambda world: repr(sorted(world.state()["refunds"])))
    stack = Stack()
    results = asyncio.run(
        live_proof.prove_live(
            authoring=tmp_path, contract=_contract(modes), runtime=stack, provider=stack, factory=stack, work=tmp_path,
            **options,
        )
    )
    return results, stack


def test_service_tool_control_runs_and_fails_on_the_runtime(monkeypatch, tmp_path):
    results, stack = _prove(monkeypatch, tmp_path, {"lookup_order": "service", "refund_order": "service"})

    live = results["refund_case"]
    assert live["assumed"] == []
    assert [(one["name"], one["held"]) for one in live["with_solution"]] == [("refund_once", True), ("looked_up", True)]
    control = live["controls"][0]
    assert control["name"] == "two_refunds"
    assert control["failed"] is True and control["assumed"] == []
    assert "refund" in stack.worlds[0].forwarded
    # A reset before the reference, the empty run and the control, and one after.
    assert stack.resets == 4
    assert live_proof.load_live(tmp_path)["refund_case"] == live

    proof = live_proof.apply_live(Proof(deferred=True), live)
    assert proof.proved is True and proof.blocked_reason() is None
    assert live_proof.live_summary(results) == "played 1 cases on the live runtime; the reference passed in 1"


CALLED_ONCE = check(
    "n = sum(1 for c in calls if c.ok and c.name == 'refund_order' and c.arguments.get('order_id') == 'O-10')\n"
    "return None if n == 1 else f'{n} refund calls'"
)


def test_in_process_tool_stays_assumed(monkeypatch, tmp_path):
    # The refund runs inside the agent's own process: it has no endpoint, so its step is recorded
    # unrun and only a check that reads the calls can hold.
    goals = dict(refund_once=CALLED_ONCE, looked_up=REFUND_CATALOGUE["looked_up"])
    results, stack = _prove(monkeypatch, tmp_path, {"lookup_order": "service", "refund_order": "import"}, goals)
    assert "refund" not in stack.worlds[0].forwarded

    live = results["refund_case"]
    assert live["assumed"] == ["refund_order"]
    assert live["controls"][0]["assumed"] == ["refund_order"]
    proof = live_proof.apply_live(Proof(deferred=True), live)
    assert proof.proved is False and proof.kept_as_written is True


def test_no_tool_proxy_plays_nothing(monkeypatch, tmp_path):
    contract = _contract({"lookup_order": "service", "refund_order": "service"})
    assert live_proof.service_endpoints(contract, {"target_http": "x"}) == {}
    assert live_proof.service_endpoints(contract, {"tool_proxy": "y"}) == {"lookup_order": "lookup", "refund_order": "refund"}


RAISES_ON_TWO = check(
    "if len(world.data['refunds']) > 1:\n    raise KeyError('second refund')\n"
    "return None if len(world.data['refunds']) == 1 else 'no refund'"
)


def test_a_check_that_raises_under_a_control_is_broken_not_failing(monkeypatch, tmp_path):
    install(monkeypatch)
    goals = dict(refund_once=RAISES_ON_TWO, looked_up=REFUND_CATALOGUE["looked_up"])
    proof = module.prove(refund_case(), catalogue(**goals), tmp_path)

    control = proof.controls[0]
    assert control["failed"] is False and control["broken"] == ["refund_once"]
    assert proof.proved is False
    assert proof.blocked_reason() == "broken_check"
    assert proof.broken == ["refund_once (under negative control two_refunds)"]


def test_a_control_that_could_not_be_played_is_no_evidence():
    proof = Proof(ready=True, solvable=True, vacuous=False, outcome="change")
    proof.controls = [{"name": "two_refunds", "why": "", "failed": None, "held": [], "assumed": []}]

    assert proof.ran_controls == []
    assert proof.proved is False
    assert proof.blocked_reason() == "control_not_run"


def test_a_control_that_crashes_on_the_runtime_keeps_the_case_live_proof(monkeypatch, tmp_path):
    real = live_proof._play
    played = []

    def play(scenario, world, steps):
        played.append(len(steps))
        if len(played) == 3:  # reference, empty run, then the control
            raise ConnectionError("tools service went away")
        return real(scenario, world, steps)

    monkeypatch.setattr(live_proof, "_play", play)
    results, _stack = _prove(monkeypatch, tmp_path, {"lookup_order": "service", "refund_order": "service"})

    live = results["refund_case"]
    assert [one["held"] for one in live["with_solution"]] == [True, True]
    control = live["controls"][0]
    assert control["failed"] is None and "tools service went away" in control["error"]
    proof = live_proof.apply_live(Proof(deferred=True), live)
    assert proof.proved is False and proof.blocked_reason() == "control_not_run"


class _Toolbox:
    """The real MCP Toolbox adapter, served on a free local port, its SQL standing in for a dict datastore."""

    TOOLS = {
        "lookup_order": {"kind": "postgres-sql", "statement": "lookup", "parameters": [{"name": "order_id", "type": "string"}]},
        "refund_order": {"kind": "postgres-sql", "statement": "refund", "parameters": [{"name": "order_id", "type": "string"}]},
    }

    def __init__(self, monkeypatch, tmp_path, store: dict) -> None:
        import json
        import socket
        import threading
        import time

        import uvicorn

        from fi.alk.harness import mcp_toolbox_adapter

        def execute(tool_name, spec, arguments, *, database_url, trace_path):
            order = store["orders"].get(arguments["order_id"])
            if spec["statement"] == "lookup":
                return json.dumps(order)
            if order is None or order["age_days"] > 14:
                raise RuntimeError("refused by policy")
            store["refunds"].append(arguments["order_id"])
            return json.dumps({"refunded": arguments["order_id"]})

        monkeypatch.setattr(mcp_toolbox_adapter, "_execute", execute)
        application = mcp_toolbox_adapter._application({"tools": self.TOOLS}, "postgresql://unused", tmp_path / "t.jsonl")
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.address = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(application, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()


def _toolbox_world(store: dict):
    import random

    from fi.alk.harness.world.handle import HostedWorld

    class ToolboxWorld(HostedWorld):
        """The hosted world handle, reading the dict datastore the toolbox writes."""

        @property
        def data(self) -> dict:
            return store

        def state(self, table=None) -> dict:
            return copy.deepcopy(store)

    return ToolboxWorld(object(), 0, random.Random(0), {}, _validate_baseline=False)


def test_a_toolbox_tool_is_played_on_the_toolbox_the_agent_reaches(monkeypatch, tmp_path):
    from fi.alk.harness.prove import play_reference_step
    from fi.alk.harness.scenario import Step

    store = copy.deepcopy(BASE)
    toolbox = _Toolbox(monkeypatch, tmp_path, store)
    try:
        endpoints = {"mcp_toolbox": SimpleNamespace(address=toolbox.address), "target_http": "x"}
        served = live_proof.served_by_toolbox(endpoints)
        assert served == {"lookup_order", "refund_order"}
        # A tool the contract places in the agent's own process is not the toolbox's, whatever its name.
        assert live_proof.service_endpoints(_contract({"lookup_order": "unreachable", "refund_order": "import"}), endpoints, served) == {
            "lookup_order": "mcp:lookup_order"
        }
        world = _toolbox_world(store)
        bound = live_proof.service_endpoints(_contract({"lookup_order": "service", "refund_order": "unreachable"}), endpoints, served)
        world.bind_tools("", bound, ["lookup_order", "refund_order"], mcp_toolbox=toolbox.address)

        looked_up = world.call("lookup_order", {"order_id": "O-10"})
        assert looked_up.ok and looked_up.result == {"age_days": 10, "total": 42.5}
        refused = world.call("refund_order", {"order_id": "O-20"})
        assert refused.ok is False and refused.refused is True and "refused by policy" in refused.error

        played = play_reference_step(world, Step.model_validate(REFUND_O10))
        assert played.ok and played.name == "refund_order"
        assert store["refunds"] == ["O-10"]
    finally:
        toolbox.close()


def test_a_toolbox_case_is_proved_on_the_live_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(live_proof, "load_scenarios", lambda path: [refund_case()])
    monkeypatch.setattr(live_proof, "load_catalogue", lambda path: catalogue(**REFUND_CATALOGUE))
    monkeypatch.setattr(live_proof, "_digest", lambda world: repr(sorted(world.state()["refunds"])))
    stack = Stack()
    toolbox = _Toolbox(monkeypatch, tmp_path, stack.store)
    try:
        # A toolbox agent's bundle has no tools service, so no tool proxy: only the toolbox is up.
        stack.endpoints = {"mcp_toolbox": SimpleNamespace(address=toolbox.address), "target_http": "x"}

        async def create(runtime, *, rng):
            return _toolbox_world(stack.store)

        stack.create = create
        results = asyncio.run(
            live_proof.prove_live(
                authoring=tmp_path,
                contract=_contract({"lookup_order": "unreachable", "refund_order": "unreachable"}),
                runtime=stack,
                provider=stack,
                factory=stack,
                work=tmp_path,
            )
        )
    finally:
        toolbox.close()

    live = results["refund_case"]
    assert live["assumed"] == []
    assert [(one["name"], one["held"]) for one in live["with_solution"]] == [("refund_once", True), ("looked_up", True)]
    assert live["controls"][0]["failed"] is True
    proof = live_proof.apply_live(Proof(deferred=True), live)
    assert proof.proved is True and proof.blocked_reason() is None


def test_a_toolbox_that_cannot_be_listed_binds_nothing():
    import socket

    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        address = f"http://127.0.0.1:{closed.getsockname()[1]}"
    endpoints = {"mcp_toolbox": SimpleNamespace(address=address), "target_http": "x"}
    served = live_proof.served_by_toolbox(endpoints)
    assert served == set()
    assert live_proof.service_endpoints(_contract({"lookup_order": "service", "refund_order": "service"}), endpoints, served) == {}


SERVICE_MODES = {"lookup_order": "service", "refund_order": "service"}


def test_only_the_requested_cases_are_played_live(monkeypatch, tmp_path):
    results, stack = _prove(monkeypatch, tmp_path, SERVICE_MODES, keys={"another_case"})
    assert results == {} and all(not world.forwarded for world in stack.worlds)
    assert live_proof.load_live(tmp_path) == {}

    results, _ = _prove(monkeypatch, tmp_path, SERVICE_MODES, keys={"refund_case"})
    assert results["refund_case"]["assumed"] == []


def test_no_case_starts_once_the_time_budget_is_spent(monkeypatch, tmp_path):
    import time

    results, stack = _prove(monkeypatch, tmp_path, SERVICE_MODES, until=time.time() - 1)
    assert results == {} and all(not world.forwarded for world in stack.worlds)

    results, _ = _prove(monkeypatch, tmp_path, SERVICE_MODES, until=time.time() + 600)
    assert results["refund_case"]["assumed"] == []


def test_forgetting_live_results_keeps_the_other_cases(tmp_path):
    live_proof.write_live(tmp_path, {"one": {"assumed": []}, "two": {"assumed": []}})
    live_proof.forget_live(tmp_path, {"one"})
    assert live_proof.load_live(tmp_path) == {"two": {"assumed": []}}
    live_proof.forget_live(tmp_path, None)
    assert live_proof.load_live(tmp_path) == {}
    live_proof.forget_live(tmp_path / "absent", None)


# A support desk whose tools its own `tools-api` service serves, as its sealed contract records them: each tool's route
# is on its entrypoint (`endpoint`), never on the tool itself.
_SERVICE_DESK = {
    "agent": "support",
    "tools": [{"name": name, "args": ["order_id"]} for name in ("get_order", "list_refunds", "issue_refund")]
    + [{"name": "summarise", "args": []}, {"name": "build_reply", "args": []}, {"name": "search_kb", "args": []}],
    "tool_entrypoints": [
        {"tool": "get_order", "mode": "service", "service": "tools-api", "endpoint": "orders/get", "method": "POST",
         "module": "ledger", "callable": "_order"},
        {"tool": "list_refunds", "mode": "service", "service": "tools-api", "endpoint": "/refunds/list/"},
        {"tool": "issue_refund", "mode": "service", "service": "tools-api", "endpoint": "refunds/issue"},
        {"tool": "summarise", "mode": "import", "module": "agent", "callable": "summarise"},
        {"tool": "build_reply", "mode": "construct", "module": "agent", "callable": "reply", "factory": "Agent()"},
    ],
}


def test_every_entrypoint_mode_reads_its_route_from_the_sealed_contract():
    from fi.alk.harness.contract import AgentContract

    contract = AgentContract.model_validate(_SERVICE_DESK)
    endpoints = {"tool_proxy": "http://proxy", "mcp_toolbox": "http://toolbox"}

    bound = live_proof.service_endpoints(contract, endpoints, {"search_kb", "summarise"})

    # Service tools play on their entrypoint's route behind the proxy; a tool the toolbox serves plays there; tools in
    # the agent's own process (import, construct) have no seam outside it and stay unbound.
    assert bound == {
        "get_order": "orders/get",
        "list_refunds": "refunds/list",
        "issue_refund": "refunds/issue",
        "search_kb": "mcp:search_kb",
    }
    world = SimpleNamespace(runtime_tools=set(), endpoint_for={})
    world.runtime_tools, world.endpoint_for = set(contract.tool_names()) | set(bound), dict(bound)
    assert [name for name in sorted(contract.tool_names()) if module.executes(world, name)] == [
        "get_order", "issue_refund", "list_refunds", "search_kb",
    ]


def test_a_control_whose_steps_never_ran_is_not_read_as_failed():
    from fi.alk.harness.checks import Outcome
    from fi.alk.harness.controls import played_control

    wrong = control("wrong_order", "looks up the wrong order", [LOOKUP_O10])
    failing = [Outcome("looked_up", False, "no lookup call")]

    unrun = played_control(wrong, failing, ["lookup_order"])
    assert unrun["ran"] is False and unrun["failed"] is None

    ran = played_control(wrong, failing, [])
    assert ran["ran"] is True and ran["failed"] is True

    proof = Proof(controls=[unrun])
    assert proof.ran_controls == [] and proof.controls_failed is False and proof.proved is False


def test_a_reference_the_service_fails_is_played_but_never_proved(monkeypatch, tmp_path):
    # The case's order is one the tools service always fails to refund (a payment timeout), and its check expects the
    # refund issued: the live runtime plays the case, the reference does not pass, and nothing reads it as proved.
    def timing_out(self, endpoint, arguments=None, *, record_as="", record=True):
        self.forwarded.append(endpoint)
        args = dict(arguments or {})
        if endpoint == "refund":
            return Call(endpoint, args, ok=False, error="504 payment_times_out")
        return Call(endpoint, args, result=self.store["orders"].get(args.get("order_id")), ok=True)

    monkeypatch.setattr(ServiceWorld, "forward", timing_out)
    results, _stack = _prove(monkeypatch, tmp_path, {"lookup_order": "service", "refund_order": "service"})

    live = results["refund_case"]
    assert live["assumed"] == [] and live["refused"]
    assert dict((one["name"], one["held"]) for one in live["with_solution"])["refund_once"] is False
    assert live_proof.live_summary(results) == "played 1 cases on the live runtime; the reference passed in 0"
    proof = live_proof.apply_live(Proof(deferred=True), live)
    assert proof.proved is False and proof.holds is False and proof.blocked_reason() == "not_solvable"


# The lookup checked as any agent may type the id, so the offline pass finds nothing too strict.
_BUILT_GOALS = dict(
    refund_once=REFUND_CATALOGUE["refund_once"],
    looked_up=check(
        "return None if any(c.name == 'lookup_order' and c.ok and same_id(c.arguments.get('order_id'), 'O-10') "
        "for c in calls) else 'O-10 never looked up'"
    ),
)


def _built(monkeypatch, tmp_path, case, *, forward=None, agent_document=None):
    """A build: its validation plays `case` on the live runtime, then the build proves its suite into the archived
    `scenario-proofs.json`, the offline pass reading service tools as assumed as it does without a runtime."""
    import json

    from fi.alk.harness import authoring_entrypoint, prove_suite

    if forward is not None:
        monkeypatch.setattr(ServiceWorld, "forward", forward)
    monkeypatch.setattr(live_proof, "load_scenarios", lambda path: [case])
    monkeypatch.setattr(live_proof, "load_catalogue", lambda path: catalogue(**_BUILT_GOALS))
    monkeypatch.setattr(live_proof, "_digest", lambda world: repr(sorted(world.state()["refunds"])))
    stack = Stack()
    asyncio.run(
        live_proof.prove_live(
            authoring=tmp_path, contract=_contract({"lookup_order": "service", "refund_order": "service"}),
            runtime=stack, provider=stack, factory=stack, work=tmp_path,
        )
    )
    install(monkeypatch, runtime_tools={"lookup_order", "refund_order"})
    monkeypatch.setattr(prove_suite, "load_scenarios", lambda path: [case])
    monkeypatch.setattr(prove_suite, "load_catalogue", lambda path: catalogue(**_BUILT_GOALS))
    authoring_entrypoint._prove_scenarios(tmp_path, None, stage="build", agent_document=agent_document)
    return json.loads((tmp_path / authoring_entrypoint.SCENARIO_PROOFS).read_text(encoding="utf-8"))["refund_case"]


def test_a_build_s_proofs_carry_the_verdict_its_live_run_gave(monkeypatch, tmp_path):
    saved = _built(monkeypatch, tmp_path, refund_case())
    assert saved["live"] is True and saved["proved"] is True and saved["proved_at"]
    assert saved["controls"][0]["ran"] is True and saved["controls"][0]["failed"] is True
    # Nothing the offline pass assumed is carried over: the live run executed every step.
    assert saved["assumed"] == [] and saved["excused"] == [] and "trial" not in saved


def test_a_build_s_case_whose_refund_the_service_fails_is_not_solvable(monkeypatch, tmp_path):
    def timing_out(self, endpoint, arguments=None, *, record_as="", record=True):
        args = dict(arguments or {})
        if endpoint == "refund":
            return Call(endpoint, args, ok=False, error="504 payment_times_out")
        return Call(endpoint, args, result=self.store["orders"].get(args.get("order_id")), ok=True)

    saved = _built(monkeypatch, tmp_path, refund_case(), forward=timing_out)
    assert saved["live"] is True and saved["proved"] is False and saved["blocked_reason"] == "not_solvable"


def test_a_build_s_case_whose_control_did_not_run_is_not_proved(monkeypatch, tmp_path):
    place = {"tool": "place_order", "arguments": {"order_id": "O-10"}}
    case = refund_case(negative_controls=[control("places_instead", "places a new order", [LOOKUP_O10, place])])
    saved = _built(monkeypatch, tmp_path, case)
    assert saved["live"] is True and saved["controls"][0]["ran"] is False and saved["controls"][0]["failed"] is None
    assert saved["proved"] is False and saved["blocked_reason"] == "control_not_run"


def test_a_later_validation_attempt_never_inherits_an_earlier_attempt_s_verdict(monkeypatch, tmp_path):
    from fi.alk.harness import authoring_runtime_validation as validation

    cases = {"now": refund_case()}
    monkeypatch.setattr(live_proof, "load_scenarios", lambda path: [cases["now"]])
    monkeypatch.setattr(live_proof, "load_catalogue", lambda path: catalogue(**_BUILT_GOALS))
    monkeypatch.setattr(live_proof, "_digest", lambda world: repr(sorted(world.state()["refunds"])))
    contract = _contract({"lookup_order": "service", "refund_order": "service"})

    def attempt():
        stack = Stack()
        return asyncio.run(
            validation.play_this_attempt(
                authoring=tmp_path, contract=contract, runtime=stack, provider=stack, factory=stack, work=tmp_path, seed=0
            )
        )

    # Attempt 1 plays the case on the live runtime.
    attempt()
    assert "refund_case" in live_proof.load_live(tmp_path)

    # A repair rewrites it to use a tool the runtime does not serve, so attempt 2 cannot play it.
    place = {"tool": "place_order", "arguments": {"order_id": "O-10"}}
    cases["now"] = scenario(
        [place], sub_goals=["refund_once"], negative_controls=[control("twice", "places two", [place, place])]
    )
    attempt()

    # Its proof is then the offline one, never attempt 1's verdict on the case as it was.
    assert "refund_case" not in live_proof.load_live(tmp_path)


class _LoggedWorld(ServiceWorld):
    """A live world whose tool proxy logs every call, a read included, in the harness's call log table."""

    def forward(self, endpoint, arguments=None, *, record_as="", record=True) -> Call:
        self.store.setdefault("_alk_tool_trace", []).append({"name": endpoint, "arguments": dict(arguments or {})})
        return super().forward(endpoint, arguments, record_as=record_as, record=record)

    def read_only(self):
        return self


class _LoggedStack(Stack):
    async def create(self, runtime, *, rng) -> ServiceWorld:
        world = _LoggedWorld(self.store)
        self.worlds.append(world)
        return world


def test_the_outcome_is_whether_the_reference_changed_the_world_not_its_call_log(monkeypatch, tmp_path):
    reads_only = scenario(
        [LOOKUP_O10],
        name="lookup_case",
        sub_goals=["looked_up"],
        negative_controls=[control("wrong_order", "looks up another order", [LOOKUP_O20])],
    )
    monkeypatch.setattr(live_proof, "load_scenarios", lambda path: [reads_only, refund_case()])
    monkeypatch.setattr(live_proof, "load_catalogue", lambda path: catalogue(**_BUILT_GOALS))
    stack = _LoggedStack()
    results = asyncio.run(
        live_proof.prove_live(
            authoring=tmp_path, contract=_contract({"lookup_order": "service", "refund_order": "service"}),
            runtime=stack, provider=stack, factory=stack, work=tmp_path,
        )
    )
    # Every call wrote the call log; only the refund changed the world.
    assert results["lookup_case"]["outcome"] == "no_change"
    assert results["refund_case"]["outcome"] == "change"


def test_the_offline_end_state_leaves_out_the_harness_s_own_tables():
    world = SimpleNamespace(
        state=lambda: {"orders": [{"id": 1}], "_alk_tool_trace": [{"name": "lookup"}], "harness_seed_sentinel": [{}]}
    )
    assert module._end_state(world) == {"orders": [{"id": 1}]}


# A suite whose every case looks an order up first: the lookup is a step they share, not what any one is about.
LOOKS_UP_ANY = check("return None if any(c.name == 'lookup_order' and c.ok for c in calls) else 'never looked up'")
REFUNDED_ONCE = check(
    "n = sum(1 for c in calls if c.ok and c.name == 'refund_order' and same_id(c.arguments.get('order_id'), 'O-10'))\n"
    "return None if n == 1 else f'{n} refund calls'"
)
_SUITE_GOALS = dict(refund_once=REFUNDED_ONCE, looked_up=LOOKS_UP_ANY)
LOOKUP_NOTHING = {"tool": "refund_order", "arguments": {"order_id": "O-10"}}
_MISSES_LOOKUP = control("skips_lookup", "refunds without looking the order up", [LOOKUP_NOTHING])
_TWO_REFUNDS = control("two_refunds", "refunds the order twice", [LOOKUP_O10, REFUND_O10, REFUND_O10])


def _suite(size, target_control):
    target = refund_case(negative_controls=[target_control])
    others = [scenario([LOOKUP_O20], name=f"other_{n}", sub_goals=["looked_up", f"own_{n}"]) for n in range(size - 1)]
    return target, [target, *others]


def test_a_case_s_distinctive_goals_are_those_its_suite_does_not_all_name():
    target, suite = _suite(3, _TWO_REFUNDS)
    assert module.distinctive_goals(target, suite) == ["refund_once"]
    # Two cases may both be about the same goal: below three, nothing is read as shared.
    target, suite = _suite(2, _TWO_REFUNDS)
    assert module.distinctive_goals(target, suite) == []


@pytest.mark.parametrize("assumed", [False, True], ids=["ran", "assumed-steps-read-from-the-calls"])
def test_a_control_that_only_breaks_the_shared_step_is_refused(monkeypatch, tmp_path, assumed):
    install(monkeypatch, runtime_tools={"lookup_order", "refund_order"} if assumed else ())
    target, suite = _suite(3, _MISSES_LOOKUP)

    proof = module.prove(target, catalogue(**_SUITE_GOALS), tmp_path, case_goals=module.distinctive_goals(target, suite))

    assert proof.misses_case_goal is True
    assert proof.blocked_reason() == "control_misses_case_goal" and module.refuses_keeping(proof)
    assert "refund_once" in proof.why() and proof.proved is False


def test_a_control_aimed_at_the_case_s_own_goal_passes(monkeypatch, tmp_path):
    install(monkeypatch)
    target, suite = _suite(3, _TWO_REFUNDS)

    proof = module.prove(target, catalogue(**_SUITE_GOALS), tmp_path, case_goals=module.distinctive_goals(target, suite))

    assert proof.misses_case_goal is False and proof.proved is True


def test_a_small_suite_and_a_saved_proof_are_left_to_the_existing_rules(monkeypatch, tmp_path):
    install(monkeypatch)
    target, suite = _suite(2, _MISSES_LOOKUP)
    small = module.prove(target, catalogue(**_SUITE_GOALS), tmp_path, case_goals=module.distinctive_goals(target, suite))
    # Re-proving without the suite (a Validate, a read) never applies the rule, so nothing saved is demoted by it.
    reproved = module.prove(target, catalogue(**_SUITE_GOALS), tmp_path)
    assert small.misses_case_goal is False and reproved.misses_case_goal is False
    assert small.proved is True and reproved.proved is True


def test_a_rebuild_keeps_a_case_whose_control_misses_its_goal_not_proved(monkeypatch, tmp_path):
    from fi.alk.harness import nudge

    missing = module.Proof(
        ready=True, solvable=True, vacuous=False, case_goals=["refund_once"], evidence={"refund_once": ["tool_calls"]},
        controls=[{"name": "skips", "why": "", "ran": True, "failed": True, "held": [], "failing": ["looked_up"],
                   "assumed": []}],
    )
    target, suite = _suite(3, _MISSES_LOOKUP)
    monkeypatch.setattr(nudge, "load_scenarios", lambda path: suite)
    monkeypatch.setattr(nudge, "load_catalogue", lambda path: catalogue(**_SUITE_GOALS, own_0=LOOKS_UP_ANY, own_1=LOOKS_UP_ANY))
    monkeypatch.setattr(nudge, "prove", lambda *_args, **_kwargs: missing)

    problems, _not_checked, _left_out = nudge.suite_gate(tmp_path, set())

    assert problems == []
    assert any("refund_case" in one and "own goal" in one for one in nudge.unproved_without_control(tmp_path))


def test_one_control_failing_any_one_of_the_case_s_goals_is_enough():
    def ran(failing):
        return {"name": "c", "why": "", "ran": True, "failed": True, "held": [], "failing": failing, "assumed": []}

    proof = module.Proof(case_goals=["first", "second"], controls=[ran(["shared"]), ran(["second"])])
    assert proof.misses_case_goal is False
    assert module.Proof(case_goals=["first", "second"], controls=[ran(["shared"])]).misses_case_goal is True



def _agent_document(tmp_path, tools):
    import json

    path = tmp_path / "agent.json"
    path.write_text(json.dumps({"sdk_version": "x", "agent": {"tools": [{"name": one} for one in tools]}}))
    return path


def test_a_case_whose_reference_calls_a_tool_the_agent_lacks_is_never_proved(monkeypatch, tmp_path):
    # The world's service still answers the refund, live; the agent version under test no longer has the tool.
    document = _agent_document(tmp_path, ["lookup_order"])
    saved = _built(monkeypatch, tmp_path, refund_case(), agent_document=document)

    assert saved["live"] is True and saved["proved"] is False
    assert saved["blocked_reason"] == "tool_not_on_agent" and saved["refused"] is True
    assert "refund_order" in saved["why"]


def test_a_case_the_agent_s_tools_cover_is_proved(monkeypatch, tmp_path):
    saved = _built(monkeypatch, tmp_path, refund_case(), agent_document=_agent_document(tmp_path, ["lookup_order", "refund_order"]))
    assert saved["proved"] is True and saved["blocked_reason"] is None


@pytest.mark.parametrize("body", [None, "not json", '{"agent": {}}', '{"agent": {"tools": [{"label": "x"}]}}'])
def test_a_missing_or_unreadable_agent_document_skips_the_check(tmp_path, body, caplog):
    from fi.alk.harness.prove_suite import agent_tools

    path = tmp_path / "agent.json"
    if body is not None:
        path.write_text(body)
    assert agent_tools(path) is None
    assert agent_tools(None) is None
    assert "agent document not read" in caplog.text
