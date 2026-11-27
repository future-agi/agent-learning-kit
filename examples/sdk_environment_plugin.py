"""Register and run one custom simulation environment without credentials."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import fi.alk.simulate as S
from fi.simulate.environments.base import EnvironmentManifest


class OrderTarget:
    async def reply(self, order_id: str) -> str:
        return f"Order {order_id} is packed and leaves today."


@S.register_environment("order_status_world")
class OrderStatusEnvironment:
    manifest = EnvironmentManifest(
        name="order_status_world",
        world_kinds=["order_status"],
    )

    async def run(
        self,
        spec: S.SimulationSpec,
        *,
        target: OrderTarget,
        **_: Any,
    ) -> S.TestReport:
        persona = spec.scenario.dataset[0]
        answer = await target.reply("A-104")
        return S.TestReport(
            results=[
                S.TestCaseResult(
                    persona=persona,
                    transcript=f"Customer: Where is order A-104?\nAgent: {answer}",
                    messages=[{"role": "assistant", "content": answer}],
                    metadata={"environment": self.manifest.name},
                )
            ]
        )


def build_spec() -> S.SimulationSpec:
    return S.SimulationSpec(
        run_id="custom_environment_quickstart",
        environment=S.EnvironmentSpec(
            adapter="order_status_world",
            world_kind="order_status",
        ),
        target=S.AgentEndpointSpec(adapter="callable"),
        simulator=S.SimulatorPolicySpec(adapter="synthetic_user"),
        scenario=S.Scenario(
            name="order-status",
            dataset=[
                S.Persona(
                    persona={"name": "Morgan"},
                    situation="Ask where order A-104 is.",
                    outcome="Receive a shipment status.",
                )
            ],
        ),
    )


def run(output_path: str | Path | None = None) -> dict[str, Any]:
    report = asyncio.run(S.SimulationRunner().run(build_spec(), target=OrderTarget()))
    payload = {
        "environment": "order_status_world",
        "status": str(report.status),
        "transcript": report.test_cases[0].result.transcript,
    }
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


if __name__ == "__main__":
    import sys

    destination = sys.argv[1] if len(sys.argv) > 1 else None
    result = run(destination)
    print(json.dumps(result, indent=2, sort_keys=True))
