#!/usr/bin/env python3
"""Prove the voice providers answer before a run spends 25 minutes discovering they do not.

A hosted run costs roughly 13 minutes of provisioning before the first word is spoken, so a key
that is out of credit is found at the worst possible moment and presents as an agent fault. This
asks each provider a trivial question and reports the HTTP truth.

    python3 probe_voice_providers.py            # reads keys from the environment
    python3 probe_voice_providers.py --json     # machine readable

Exit 0 when every configured provider answered, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _post(url: str, headers: dict, body: bytes, timeout: int = 30) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:400]
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        return 0, str(exc).encode()[:400]


def probe_cartesia(key: str) -> dict:
    status, body = _post(
        "https://api.cartesia.ai/tts/bytes",
        {
            "X-API-Key": key,
            "Cartesia-Version": "2024-06-10",
            "Content-Type": "application/json",
        },
        json.dumps(
            {
                "model_id": "sonic-2",
                "transcript": "test",
                "voice": {"mode": "id", "id": "a0e99841-438c-4a64-b679-ae501e7d6091"},
                "output_format": {
                    "container": "wav",
                    "encoding": "pcm_f32le",
                    "sample_rate": 44100,
                },
            }
        ).encode(),
    )
    note = ""
    if status == 402:
        note = "OUT OF CREDIT: the simulator will emit transcript text and no audio at all"
    return {"provider": "cartesia", "status": status, "ok": status == 200, "note": note}


def probe_deepgram_tts(key: str) -> dict:
    status, body = _post(
        "https://api.deepgram.com/v1/speak?model=aura-asteria-en",
        {"Authorization": f"Token {key}", "Content-Type": "application/json"},
        json.dumps({"text": "This is a test."}).encode(),
    )
    return {
        "provider": "deepgram-tts",
        "status": status,
        "ok": status == 200,
        "note": "aura is one voice, so every persona sounds identical" if status == 200 else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = []
    if os.environ.get("CARTESIA_API_KEY"):
        results.append(probe_cartesia(os.environ["CARTESIA_API_KEY"]))
    if os.environ.get("DEEPGRAM_API_KEY"):
        results.append(probe_deepgram_tts(os.environ["DEEPGRAM_API_KEY"]))

    if not results:
        print("no CARTESIA_API_KEY or DEEPGRAM_API_KEY set: nothing to probe", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for entry in results:
            mark = "ok " if entry["ok"] else "FAIL"
            print(f"  [{mark}] {entry['provider']:14} http={entry['status']} {entry['note']}")
    return 0 if all(entry["ok"] for entry in results) else 1


if __name__ == "__main__":
    sys.exit(main())
