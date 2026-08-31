#!/usr/bin/env python3
"""Decide whether a finished voice call actually produced what the platform displays.

Run this before believing any green run. Every check here exists because its absence once read
as something else: a mute simulator read as a stalled agent, and a transcript whose turns carried
row indexes instead of speech timing rendered every conversation metric as zero.

    python3 check_call_evidence.py transcript.json [--receipt receipt.json]

Exit 0 when the evidence is complete, 1 when it is not. Every failure names what to look at.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


def _load(path: str) -> object:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def check_transcript(body: object) -> list[str]:
    faults: list[str] = []
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list) or not messages:
        return ["transcript has no messages: the call produced nothing to grade"]

    roles = {str(m.get("role")) for m in messages if isinstance(m, dict)}
    if "user" not in roles or "assistant" not in roles:
        faults.append(
            f"only one side spoke (roles={sorted(roles)}): a call needs both to be gradeable"
        )

    def timed(m: dict) -> bool:
        return isinstance(m.get("started_speaking_at"), (int, float))

    caller = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    agent = [m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"]

    if caller and not any(timed(m) for m in caller) and any(timed(m) for m in agent):
        faults.append(
            "caller turns carry text but no started_speaking_at while agent turns do: the "
            "simulator produced NO AUDIO. Check the TTS key (a dead one returns 402) before "
            "blaming the agent, which only heard silence"
        )
    if not any(timed(m) for m in messages):
        faults.append(
            "no message carries started_speaking_at: the platform derives talk ratio, WPM and "
            "latency from these and will render every metric as zero"
        )
    return faults


def check_receipt(body: object) -> list[str]:
    faults: list[str] = []
    if not isinstance(body, dict):
        return ["receipt is not an object"]
    call = body.get("call") or {}
    if not call.get("transcript_artifact"):
        faults.append("receipt has no transcript_artifact")
    if not call.get("recording_artifacts"):
        faults.append("receipt has no recording_artifacts: nothing to listen back to")
    if not isinstance(call.get("turns"), int) or call.get("turns", 0) < 2:
        faults.append(f"turns={call.get('turns')}: not a conversation")
    sub_goals = body.get("sub_goals")
    if not isinstance(sub_goals, list) or not sub_goals:
        faults.append("receipt carries no sub_goals: nothing was graded")
    else:
        unjudged = [g.get("name") for g in sub_goals if g.get("held") is None]
        if len(unjudged) == len(sub_goals):
            faults.append(
                f"every sub-goal is unjudged ({unjudged}): the call ended before grading"
            )
    return faults


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript")
    parser.add_argument("--receipt")
    args = parser.parse_args()

    faults = check_transcript(_load(args.transcript))
    if args.receipt:
        faults += check_receipt(_load(args.receipt))

    if faults:
        print("call evidence INCOMPLETE:")
        for fault in faults:
            print(f"  - {fault}")
        return 1
    print("call evidence complete: both sides spoke, turns are timed, sub-goals were judged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
