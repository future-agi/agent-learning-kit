"""What a spoken run leaves behind, beyond whether it passed.

ALK measures a great deal about a call and writes it into its own report: seventeen metrics per
case, what the simulated caller cost and which model it ran on, why the call ended, which LiveKit
room it happened in, four separate audio tracks, and a declaration of what each evidence source
can actually prove. None of that was reaching the harness, which kept one boolean and a wav.

So this reads that report and carries it through. It does not compute anything: everything here
was already measured by the thing that placed the call, and recomputing it would be a second
opinion nobody asked for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Where the voice runner writes. Its own directory, because the call belongs to it.
ACCEPTANCE = Path("artifacts/simulation-acceptance")

# Which recording to prefer, best first. Both voices on one track beats either alone, because
# the questions asked of a call are mostly about the interaction: whether the agent talked over
# the caller, how long it left them waiting, whether what it heard was what was said.
TRACKS = (
    ("stereo", "audio_stereo_path"),
    ("combined", "audio_combined_path"),
    ("caller", "audio_input_path"),
    ("agent", "audio_output_path"),
)


def newest_report(started: float) -> dict[str, Any]:
    """The voice runner's report for the call that just happened, or nothing.

    Only a report written after this run began counts. The newest file on disk is otherwise last
    week's call wearing today's verdict, which is the kind of mistake that is never noticed
    because the numbers look plausible.
    """
    if not ACCEPTANCE.exists():
        return {}
    newest: tuple[float, Path] | None = None
    for report in ACCEPTANCE.glob("run_*/*/report.json"):
        written = report.stat().st_mtime
        if written >= started and (newest is None or written > newest[0]):
            newest = (written, report)
    if newest is None:
        return {}
    try:
        loaded = json.loads(newest[1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    cases = loaded.get("results") or []
    return cases[0] if cases else {}


def tracks_in(case: dict[str, Any]) -> list[dict[str, str]]:
    """Every recording of this call that exists, best first.

    Several are written and any of them can be missing: a provider that did not return its own
    copy, a track that never carried audio, a run that stopped early. Offering the list rather
    than one path is what lets the page fall back instead of showing a broken player.
    """
    found: list[dict[str, str]] = []
    for label, key in TRACKS:
        path = case.get(key)
        if path and Path(path).exists():
            found.append({"label": label, "path": str(path)})
    # The provider's own recording, which survives when the room's tracks do not.
    for artifact in (case.get("metadata") or {}).get("provider_artifacts") or []:
        path = artifact.get("path")
        if artifact.get("type") == "audio" and path and Path(path).exists():
            found.append({"label": f"{artifact.get('artifact_id', 'provider')}", "path": str(path)})
    return found


def measured(case: dict[str, Any]) -> dict[str, Any]:
    """What ALK measured about this call, in the shape the page reads.

    Everything optional, because a report from a run that failed early has most of it missing and
    a page that assumes otherwise shows nothing at all rather than the part that is there.
    """
    metadata = case.get("metadata") or {}
    report = (case.get("evaluation") or {}).get("agent_report") or metadata.get(
        "agent_report_summary"
    ) or {}
    summary = report.get("summary") or {}
    usage = metadata.get("simulator_model_usage") or []
    first = usage[0] if isinstance(usage, list) and usage else {}
    return {
        "score": report.get("score"),
        "threshold": report.get("threshold"),
        "scored_pass": report.get("passed"),
        "metrics": summary.get("metric_averages") or {},
        "stop_reason": metadata.get("stop_reason"),
        "status": metadata.get("status"),
        "room": metadata.get("room_name"),
        "provider": metadata.get("target_provider"),
        "call_id": metadata.get("vapi_call_id") or metadata.get("provider_call_id"),
        "simulator": {
            "model": first.get("model"),
            "provider": first.get("provider"),
            "input_tokens": first.get("input_tokens"),
            "cached_tokens": first.get("input_cached_tokens"),
            "output_tokens": first.get("output_tokens"),
        },
        # What each source claims it can prove. Worth showing: a metric derived from a source
        # that does not report latency is not a measurement, and the report says which is which.
        "evidence": [
            {
                "source": one.get("source_id"),
                "adapter": one.get("adapter"),
                "available": one.get("available"),
                "proves": sorted(
                    key for key, held in (one.get("capabilities") or {}).items() if held
                ),
            }
            for one in metadata.get("evidence") or []
        ],
    }
