"""Out-of-the-box task benchmark — credential-free, deterministic (beat-HUD B4).

Loads the shipped ``support_starter`` task dataset, compiles it (every task's
objective must carry a deterministic anchor + Goodhart guards, or compilation
fails), and runs a scripted agent across it via ``tasks.run_benchmark`` on the
fixture lane. Prints a scored, honest benchmark result:

  * deterministic + credential-free (no keys, no network — the gate's fixture
    lane); same input -> identical score;
  * each per-task result stamped with its HONEST execution_class (conversation/
    tool_api are executable; the browser task is typed_only) and the run's
    evidence_class (captured_fixture); no fixture result is ever labeled live.

Run a real agent instead by passing ``--agent llm`` with a model key in env; the
default scripted agent keeps this example runnable anywhere with zero setup.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agent_learning import tasks

DATASET_PATH = Path(__file__).parent / "task_datasets" / "support_starter.json"
OUTPUT_KIND = "agent-learning.task-benchmark-example.v1"

# A deterministic scripted agent whose single reply name-drops the anchor tokens
# the starter tasks check for (policy / help / order / human / webhook) — enough
# for the fixture lane to score without any model or network.
SCRIPTED_REPLY = (
    "Hello, happy to help. Our refund policy is at /help/refunds with a 30-day "
    "window. For your order status I will check the order tool and report the "
    "real status. If needed I will escalate you to a human. The webhooks docs "
    "are at /docs/webhooks."
)


def run(output_path: str | Path | None = None, *, agent: dict | None = None) -> dict[str, Any]:
    dataset = tasks.load_task_dataset(DATASET_PATH)
    agent = agent or {"type": "scripted", "content": SCRIPTED_REPLY}
    result = tasks.run_benchmark(dataset, agent, evidence_class="captured_fixture")

    payload: dict[str, Any] = {
        "kind": OUTPUT_KIND,
        "status": "passed",
        "exit_code": 0,
        "dataset_name": result["dataset_name"],
        "dataset_version": result["dataset_version"],
        "coverage": dataset["coverage"],
        "aggregate": result["aggregate"],
        "per_task": result["per_task"],
    }
    if output_path is not None:
        out = Path(output_path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return payload


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    destination = args[0] if args else None
    run(destination)
