"""A principled refusal must not be reported as a crash.

The environment stage declining to build is the harness working: the submitted repository ships
no runnable seam, and building one anyway would mean inventing agent behaviour, producing a world
that grades nothing and looks green. That decision was reaching the control plane as
`guest_crashed` in the `infrastructure` domain, because the guest runs
`authoring && bundle && run` as one shell chain: a non-zero authoring exit short-circuits it, the
run entrypoint that owns the outbound channel never starts, and an exit code is all that is left.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from fi.alk.harness import authoring_entrypoint as ae
from fi.alk.harness.build import (
    EnvironmentNotBuildable,
    record_refusal,
    refusal_at,
    require_buildable,
)
from fi.alk.harness.job import FailureDomain, HarnessStage

# The platform's HarnessRetrySerializer.retryable_domains ChoiceField. Written out because it is
# a cross-repo contract: this test is what notices if `agent` is ever added to it.
PLATFORM_RETRYABLE_DOMAINS = {"infrastructure", "connectivity", "platform_sync"}


def _contract(*names: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        agent="a",
        modality="chat",
        tools=[SimpleNamespace(name=n) for n in names],
        tool_entrypoints=[],
        runtime=None,
        dependencies=[],
    )


def test_the_refusal_keeps_its_reasons_as_data():
    """Flattened to a string it is a log line somebody has to go looking for. Each entry names one
    tool and what the repository must expose for it, which is the only actionable part."""
    with pytest.raises(EnvironmentNotBuildable) as raised:
        # A source root, so the only problems reported are the per-tool ones under test.
        require_buildable(_contract("list_notes", "create_note"), "/somewhere/repo")
    assert len(raised.value.problems) == 2
    assert all("no runnable shipped entrypoint" in one for one in raised.value.problems)
    assert "list_notes" in str(raised.value)


def test_the_refusal_survives_the_process_boundary(tmp_path):
    """The stage that decides this and the process that reports it are different processes, and
    only an exit status crosses between them."""
    record_refusal(tmp_path, ["list_notes: expose the real implementation"])
    assert refusal_at(tmp_path) == ["list_notes: expose the real implementation"]


def test_no_refusal_recorded_is_not_a_refusal(tmp_path):
    assert refusal_at(tmp_path) == []


def test_an_unreadable_refusal_says_so_rather_than_reading_as_none(tmp_path, caplog):
    import fi.alk.harness.build as build_module

    (tmp_path / "environment-refusal.json").write_text('{"problems":', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=build_module.__name__):
        assert refusal_at(tmp_path) == []
    assert "could not be read" in caplog.text


class _Recorder:
    """Stands in for the outbound adapter, keeping what it was asked to send."""

    sent: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def deadline(self):
        return None

    async def emit_terminal(self, *, stage, reason=None, failure=None):
        type(self).sent = {"stage": stage, "failure": failure}
        return True

    async def drain(self, *, complete, deadline=None):
        type(self).sent["drained"] = True
        return False


def _emit(monkeypatch, problems):
    import fi.alk.harness.hosted_entrypoint as he
    import fi.alk.harness.outbound as ob

    _Recorder.sent = {}
    monkeypatch.setattr(he, "OutboundAdapter", _Recorder)
    # load_capabilities and build_transport are dataclass FIELDS holding callables, so the
    # instance takes the field default and patching the class attribute does nothing. Patch what
    # those defaults call instead.
    monkeypatch.setattr(ob, "load_capabilities", lambda *a, **k: object())
    monkeypatch.setattr(ob, "RequestsTransport", lambda *a, **k: object())
    for name in ("EventsClient", "ResultsClient", "ArtifactsClient"):
        monkeypatch.setattr(ob, name, lambda *a, **k: object())
    monkeypatch.setattr(
        he.HostedEntrypointDeps, "build_events_spool", lambda self, d: object(), raising=False
    )
    monkeypatch.setattr(
        he.HostedEntrypointDeps, "peek_secret_values", lambda self: (), raising=False
    )
    asyncio.run(ae._report_refusal(problems))
    return _Recorder.sent


def test_a_declining_stage_reports_its_own_code_and_not_a_crash(monkeypatch):
    """The whole point. `guest_crashed` sends an operator to Daytona, the image and the network,
    every one of which is healthy, while the remedy sits in a log they have no reason to open."""
    sent = _emit(monkeypatch, ["list_notes: expose the real implementation"])
    failure = sent["failure"]
    assert failure["code"] == ae.REFUSAL_CODE == "environment_not_buildable"
    assert failure["code"] != "guest_crashed"
    assert sent["stage"] is HarnessStage.FAILED
    assert failure["stage"] == HarnessStage.GENERATING_ENVIRONMENT.value
    assert sent.get("drained"), "an unspooled terminal event never reaches the platform"


def test_the_refusal_is_owned_by_the_submitted_agent_and_cannot_be_retried(monkeypatch):
    """`infrastructure` is a retryable domain, so classifying a refusal there spends a second
    sandbox re-deriving the same correct answer. `agent` is not in the platform's retryable set,
    so the domain alone makes it non-retryable; nothing separate has to remember to."""
    failure = _emit(monkeypatch, ["list_notes: expose it"])["failure"]
    assert failure["domain"] == FailureDomain.AGENT.value == "agent"
    assert failure["domain"] not in PLATFORM_RETRYABLE_DOMAINS


def test_the_per_tool_remedy_travels_with_the_failure(monkeypatch):
    """The terminal failure shape is {domain, stage, code, message} with extra="forbid", so there
    is no `details` to put this in. It rides in the message or it does not reach the operator."""
    problems = [
        "list_notes: no runnable shipped entrypoint was identified; expose the real implementation",
        "create_note: no runnable shipped entrypoint was identified; expose the real implementation",
    ]
    message = _emit(monkeypatch, problems)["failure"]["message"]
    for one in problems:
        assert one in message
    assert "inventing agent behaviour" in message


def test_a_successful_authoring_run_reports_nothing(monkeypatch, tmp_path):
    """A refusal document only exists when a stage wrote one, so a clean run cannot emit this."""
    assert refusal_at(tmp_path) == []


def _job_document(tmp_path):
    job = {
        "job_id": "j",
        "run_id": "r",
        "attempt_id": "a",
        "scenario_count": 1,
        "agent": {"connector": "auto"},
        "metadata": {"agent_name": "acme"},
    }
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return path


def _run_main(monkeypatch, tmp_path, status, *, refusal):
    """Drive main() with the authoring run stubbed, to test the wiring and nothing else."""
    reported = []

    async def _auto(namespace):
        if refusal:
            record_refusal(tmp_path / "out", ["list_notes: expose it"])
        return status

    async def _report(problems):
        reported.append(problems)
        return True

    monkeypatch.setattr(ae, "_auto", _auto)
    monkeypatch.setattr(ae, "_report_refusal", _report)
    monkeypatch.setattr(ae, "_persist_authored_scenario_count", lambda *a, **k: None)
    monkeypatch.setattr(ae.HarnessJob, "model_validate", classmethod(lambda cls, body: _FakeJob()))
    (tmp_path / "out").mkdir(exist_ok=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    code = ae.main(
        [
            str(_job_document(tmp_path)),
            "--source",
            str(tmp_path / "src"),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    return code, reported


class _FakeJob:
    scenario_count = 1
    metadata: dict = {"agent_name": "acme"}


def test_a_failed_authoring_run_reports_the_refusal_it_recorded(monkeypatch, tmp_path):
    """The wiring, which is the part a unit test of the emitter cannot see: a stage that declined
    and a process that never looks for the decline is the same silence as before."""
    code, reported = _run_main(monkeypatch, tmp_path, 1, refusal=True)
    assert code == 1, "the refusal is still a failure; only its description changes"
    assert reported == [["list_notes: expose it"]]


def test_a_failure_that_is_not_a_refusal_reports_nothing(monkeypatch, tmp_path):
    """Only a recorded refusal is reported this way. A genuine crash has no document and must
    keep whatever the control plane makes of a non-zero exit."""
    code, reported = _run_main(monkeypatch, tmp_path, 1, refusal=False)
    assert code == 1
    assert reported == []


def test_a_successful_run_reports_no_refusal(monkeypatch, tmp_path):
    code, reported = _run_main(monkeypatch, tmp_path, 0, refusal=False)
    assert code == 0
    assert reported == []
