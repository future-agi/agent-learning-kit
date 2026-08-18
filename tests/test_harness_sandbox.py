"""Running the agent's tools where the agent's dependencies are.

Most of this is offline: what a Dockerfile is built from, how a version is read out of however
the contract phrased it, and that an agent saying nothing about how it installs is a finding
rather than a guess. Those are the parts that decide whether an agent can be run at all.

The container tests need Docker and are skipped without it, following the bench Docker lane.
They are slow -- an image build is the cost of the thing being real -- so there is one image,
built once for the module.
"""

from __future__ import annotations

import json

import pytest

from fi.alk.bench._docker import docker_available
from fi.alk.harness.contract import Runtime
from fi.alk.harness.world import sandbox
from fi.alk.harness.world.sandbox import SandboxError

TAU = "/Users/rishavhada/Documents/futureagi/oss/tau-bench"


# --- offline: what an agent is built from -------------------------------------------------


@pytest.mark.parametrize(
    "said, wanted",
    [(">=3.11", "3.11"), ("3.10+", "3.10"), ("Python 3.12", "3.12"), ("", "3.11"), (None, "3.11")],
)
def test_a_version_is_read_out_of_however_it_was_phrased(said, wanted) -> None:
    """The contract writes this as prose, and a base image needs two numbers."""
    assert sandbox._version(said) == wanted


def test_the_agents_own_dockerfile_wins(tmp_path) -> None:
    """It is the environment its author says the code runs in. Anything generated is a guess."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.11\n")
    recipe, its_own = sandbox.dockerfile_for(tmp_path, Runtime(install="pip install -e ."))
    assert its_own and recipe.name == "Dockerfile"


def test_one_is_written_where_the_agent_ships_none(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    recipe, its_own = sandbox.dockerfile_for(
        tmp_path, Runtime(version=">=3.12", install="pip install -e .")
    )
    assert not its_own
    written = recipe.read_text()
    assert "FROM python:3.12-slim" in written
    assert "RUN pip install -e ." in written


def test_an_install_is_worked_out_where_the_contract_gives_none(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("requests\n")
    recipe, _ = sandbox.dockerfile_for(tmp_path, Runtime())
    assert "pip install -r requirements.txt" in recipe.read_text()


def test_an_agent_that_says_nothing_is_a_finding_not_a_guess(tmp_path) -> None:
    """No Dockerfile, no install command, no manifest. Its tools cannot be run, and saying so
    is the whole point -- the alternative is writing them, which tests an agent nobody has."""
    with pytest.raises(SandboxError, match="finding to report"):
        sandbox.dockerfile_for(tmp_path, Runtime())


def test_the_runner_needs_nothing_the_agents_image_may_lack() -> None:
    """It runs in whatever the agent brought, so it may import only the standard library."""
    for name in ("langchain", "fastapi", "requests", "pydantic"):
        assert name not in sandbox.SERVER


# --- with docker: the agent's own code, really running -------------------------------------

needs_docker = pytest.mark.skipif(not docker_available(), reason="docker daemon unavailable")
needs_tau = pytest.mark.skipif(
    not __import__("pathlib").Path(TAU).is_dir(), reason="tau-bench not checked out"
)


@pytest.fixture(scope="module")
def agent():
    """One container for the module. Building an image per test would cost minutes each."""
    sandbox.tear_down("pytest")
    container = sandbox.stand_up(
        "pytest", TAU, Runtime(version="3.10", install="pip install -e .")
    )
    try:
        yield container
    finally:
        sandbox.tear_down("pytest")


@pytest.fixture()
def loaded(agent):
    where = f"{TAU}/tau_bench/envs/retail/data"
    state = {
        name: json.load(open(f"{where}/{name}.json"))
        for name in ("orders", "products", "users")
    }
    sandbox.set_state(agent, state)
    return agent


CANCEL = "tau_bench.envs.retail.tools.cancel_pending_order"


@needs_docker
@needs_tau
def test_the_state_is_held_in_the_container(loaded) -> None:
    """Handed over once rather than shipped per call, which is why this is worth a container."""
    held = sandbox.get_state(loaded)
    assert {name: len(rows) for name, rows in held.items()} == {
        "orders": 1000, "products": 50, "users": 500
    }


@needs_docker
@needs_tau
def test_the_agents_own_tool_runs_and_changes_what_it_holds(loaded) -> None:
    pending = next(o for o, v in sandbox.get_state(loaded)["orders"].items()
                   if v["status"] == "pending")
    sandbox.call(loaded, CANCEL, "CancelPendingOrder.invoke",
                 {"order_id": pending, "reason": "no longer needed"}, first_arg="data")
    assert sandbox.get_state(loaded)["orders"][pending]["status"] == "cancelled"


@needs_docker
@needs_tau
def test_state_carries_across_calls(loaded) -> None:
    """The second call sees what the first did, which is the whole reason it is resident."""
    pending = next(o for o, v in sandbox.get_state(loaded)["orders"].items()
                   if v["status"] == "pending")
    args = {"order_id": pending, "reason": "no longer needed"}
    first = sandbox.call(loaded, CANCEL, "CancelPendingOrder.invoke", args, first_arg="data")
    again = sandbox.call(loaded, CANCEL, "CancelPendingOrder.invoke", args, first_arg="data")
    # The first succeeds and hands back the order; the second is refused *because* of it.
    assert "Error" not in str(first)
    assert again == "Error: non-pending order cannot be cancelled"


@needs_docker
@needs_tau
def test_a_refusal_comes_back_as_the_agent_wrote_it(loaded) -> None:
    """tau-bench reports a refusal as an ordinary string, so it is an answer and not a raise."""
    said = sandbox.call(loaded, CANCEL, "CancelPendingOrder.invoke",
                        {"order_id": "#W0", "reason": "no longer needed"}, first_arg="data")
    assert said == "Error: order not found"


@needs_docker
@needs_tau
def test_a_tool_that_raises_is_the_agent_refusing_not_the_sandbox_breaking(loaded) -> None:
    """Told apart because one is scored against the agent and the other never is."""
    from fi.alk.harness.world.sandbox import ToolRefused

    with pytest.raises(ToolRefused):
        sandbox.call(loaded, CANCEL, "CancelPendingOrder.invoke", {"nonsense": 1},
                     first_arg="data")


@needs_docker
@needs_tau
def test_a_module_that_is_not_there_is_the_sandbox_saying_so(loaded) -> None:
    from fi.alk.harness.world.sandbox import ToolRefused

    with pytest.raises((SandboxError, ToolRefused)):
        sandbox.call(loaded, "no.such.module", "whatever", {})


@pytest.mark.parametrize(
    "said, wanted",
    [
        # What one agent's contract actually recorded. A shell reads the bracket as a syntax
        # error, and the image build fails before anything else is tried.
        ("pip install -e . (from repo root; pyproject.toml present)", "pip install -e ."),
        ("uv sync --dev", "uv sync --dev"),
        # The trailing "." is the argument, not punctuation.
        ("pip install -e .", "pip install -e ."),
        ("pip install -r requirements.txt", "pip install -r requirements.txt"),
    ],
)
def test_an_install_command_survives_however_the_contract_explained_it(said, wanted) -> None:
    assert sandbox._command(said) == wanted


# --- an import failure is not a tool refusing -----------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "ModuleNotFoundError: No module named 'langchain'",
        "ImportError: cannot import name 'Thing' from 'pkg'",
        # The sandwich agent checks for its web build at module scope and raises if absent.
        "RuntimeError: Web build not found at /web/dist. Run 'make build-web'",
    ],
)
def test_a_module_that_would_not_load_is_not_a_working_refusal(said) -> None:
    """It came back as one, so both tools were marked adopted while nothing of them had run,
    and the world saved with two invented handlers reporting success."""
    from fi.alk.harness.world.tools import _never_ran

    assert _never_ran(said)


@pytest.mark.parametrize(
    "said",
    [
        "Error: order not found",
        "Error: non-pending order cannot be cancelled",
        "REFUSED: invoice 1 has already been refunded.",
    ],
)
def test_a_tool_saying_no_still_counts_as_it_working(said) -> None:
    from fi.alk.harness.world.tools import _never_ran

    assert not _never_ran(said)


# --- the image is built from the repository, not the package inside it -----------------------


@pytest.mark.parametrize(
    "root, workdir, climbs",
    [
        # The case that broke: main.py wants ../../web/dist, which an image built from the
        # package alone puts at /web, so the module cannot load and the tools read as
        # unreachable when it is the context that was too narrow.
        ("/components/python", "components/python/src", "/"),
        ("/repo", "tau_bench/envs/retail", "/repo"),
        ("/agent", ".", "/agent"),
        ("/agent", "", "/agent"),
    ],
)
def test_the_context_climbs_to_the_repository(tmp_path, root, workdir, climbs) -> None:
    from fi.alk.harness.contract import Runtime

    # Laid out for real, since the climb only happens where the path actually exists.
    base = tmp_path / root.strip("/")
    (base / "src").mkdir(parents=True, exist_ok=True)
    if workdir and workdir != ".":
        (tmp_path / workdir).mkdir(parents=True, exist_ok=True)
    found = sandbox.context_for(base, Runtime(workdir=workdir))
    assert found.is_dir()


def test_a_dockerfile_the_stage_wrote_wins(tmp_path) -> None:
    """It was written by whoever read the repository and watched the generated one fail.

    Ignoring it is how a build repeats a recipe already known not to work: one said so
    outright -- "the harness always uses its own generated Dockerfile and ignores my override".
    """
    from fi.alk.harness.contract import Runtime

    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "pyproject.toml").write_text("[project]\nname='x'\n")
    env = tmp_path / "session" / "env"
    env.mkdir(parents=True)
    (env / "Dockerfile").write_text("FROM python:3.12-slim\nRUN echo written-by-the-stage\n")

    recipe, its_own = sandbox.dockerfile_for(agent, Runtime(install="pip install -e ."), env)
    assert its_own and "written-by-the-stage" in recipe.read_text()

    # and with nothing written, the generated one is still used
    plain, generated = sandbox.dockerfile_for(agent, Runtime(install="pip install -e ."))
    assert not generated and "pip install -e ." in plain.read_text()


def test_a_setup_command_keeps_its_quoted_arguments(tmp_path) -> None:
    """Split on whitespace, `cp "a file.py" dest/` becomes three arguments and the quotes
    arrive as part of the filename."""
    from fi.alk.harness.world.workspace import run_setup

    (tmp_path / "a file.txt").write_text("x")
    (tmp_path / ".venv").mkdir()
    code, said = run_setup(tmp_path, "cp 'a file.txt' .venv/")
    assert code == 0, said
    assert (tmp_path / ".venv" / "a file.txt").exists()
