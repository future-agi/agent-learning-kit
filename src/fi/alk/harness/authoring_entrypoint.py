"""Run the established ALK authoring stages for one hosted job.

This is intentionally a thin process boundary over :func:`fi.alk.harness.cli._auto`.
Contract creation, logical environment creation, and scenario generation therefore remain the
same implementation used by the local SDK and sandbox flows.  Daytona consumes the frozen output
afterward; this command never executes scenarios itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .build import refusal_at
from .cli import _auto
from .job import FailureDomain, HarnessJob, HarnessStage
from .scenarios import load as load_written

logger = logging.getLogger(__name__)

REFUSAL_CODE = "environment_not_buildable"


async def _report_refusal(problems: list[str]) -> bool:
    """Tell the platform the environment stage declined, and why, before this process exits.

    Nothing downstream will do it. The guest runs `authoring && bundle && run` as one shell
    chain, so a non-zero authoring exit short-circuits it and the run entrypoint -- the only
    component that owns an outbound channel -- never starts. What the control plane is left with
    is an exit code, which it reports as `guest_crashed` in the `infrastructure` domain: a
    principled refusal presented as a crashed sandbox, sending an operator to look at Daytona,
    the image and the network, all of which are healthy, while the actual remedy sits in a log
    they have no reason to open. `infrastructure` is also a retryable domain, so the same correct
    refusal gets re-derived in a second sandbox.

    The domain here is `agent`: the submitted repository ships no seam to build against. That is
    not in the platform's retryable set, so this cannot be retried into the same answer twice.
    """
    from . import outbound as ob
    from .hosted_entrypoint import HostedEntrypointDeps, OutboundAdapter

    deps = HostedEntrypointDeps()
    try:
        capabilities = deps.load_capabilities()
    except ob.CapabilitiesError as exc:
        # No channel: the ordinary shape of a local run, and not an error. Hosted runs always
        # have one, so this staying quiet locally does not hide anything hosted.
        logger.info("no outbound channel to report the refusal through: %s", exc.code)
        return False

    work_directory = Path("/work")
    channel_state = ob.ChannelState()
    transport = deps.build_transport()
    retry_policy = deps.retry_policy()
    events_spool = deps.build_events_spool(work_directory)
    adapter = OutboundAdapter(
        capabilities,
        events_spool=events_spool,
        events_client=ob.EventsClient(
            capabilities,
            events_spool,
            transport,
            retry_policy=retry_policy,
            channel_state=channel_state,
        ),
        results_client=ob.ResultsClient(
            capabilities, transport, retry_policy=retry_policy, channel_state=channel_state
        ),
        artifacts_client=ob.ArtifactsClient(
            capabilities, transport, retry_policy=retry_policy, channel_state=channel_state
        ),
        channel_state=channel_state,
        extra_secret_values=deps.peek_secret_values(),
    )
    # The remedy travels in `message`, because the terminal event's failure shape is
    # {domain, stage, code, message} with extra="forbid" and has nowhere else to put it. Every
    # line names one tool and what the repository must expose for it, which is the whole value.
    await adapter.emit_terminal(
        stage=HarnessStage.FAILED,
        failure={
            "domain": FailureDomain.AGENT.value,
            "stage": HarnessStage.GENERATING_ENVIRONMENT.value,
            "code": REFUSAL_CODE,
            "message": (
                "The environment stage declined to build: the submitted repository does not "
                "expose a runnable seam for these tools, and building one would mean inventing "
                "agent behaviour, which would grade nothing and look green.\n  - "
                + "\n  - ".join(problems)
            ),
        },
    )
    await adapter.drain(complete=False, deadline=adapter.deadline())
    return True


def _persist_authored_scenario_count(
    job_path: Path, job: HarnessJob, output: Path
) -> None:
    """Keep the frozen job in sync with adjustments applied during authoring.

    The control plane may increase ``scenario_count`` while this process is already
    running.  ``_auto`` sees that adjustment and writes the larger validated suite,
    but the following Bundle V2 process reloads this on-disk job document.  Without
    reconciling it here the bundler copies the original number of scenarios and the
    platform correctly rejects preallocation because its expected count is newer.
    """
    authored_count = len(load_written(output))
    if authored_count <= 0 or authored_count == job.scenario_count:
        return
    updated = job.model_copy(update={"scenario_count": authored_count})
    temporary = job_path.with_suffix(f"{job_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(updated.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(job_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--adjustments",
        type=Path,
        help="JSONL inbox for user corrections applied at safe stage boundaries",
    )
    args = parser.parse_args(argv)

    job = HarnessJob.model_validate(json.loads(args.job.read_text(encoding="utf-8")))
    # Transport kinds such as ``archive`` and ``github`` describe how the platform acquired the
    # source.  Once extracted, the established ALK authoring pipeline must inspect it as a repo.
    source_kind = str(job.metadata.get("source_kind") or "repo")
    if source_kind not in {"repo", "spec"}:
        source_kind = "repo"
    namespace = argparse.Namespace(
        path=str(args.source.resolve()),
        name=str(job.metadata.get("agent_name") or args.source.name),
        kind=source_kind,
        out=str(args.output.resolve()),
        count=job.scenario_count,
        model=None,
        run_model=None,
        job=job,
        adjustments_path=str(args.adjustments) if args.adjustments else None,
        authoring_only=True,
    )
    status = asyncio.run(_auto(namespace))
    if status == 0:
        _persist_authored_scenario_count(args.job, job, args.output.resolve())
        return status
    problems = refusal_at(args.output.resolve())
    if problems:
        try:
            asyncio.run(_report_refusal(problems))
        except Exception:  # noqa: BLE001 - reporting must never replace the refusal itself
            logger.exception("could not report the environment refusal upward")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
