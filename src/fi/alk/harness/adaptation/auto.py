"""The adaptation without a builder session (`hosted --auto`, the platform's run mode).

One model call drafts the binding, any trajectories code cannot derive, and the policy conflicts,
from the same brief and the same mapping language the builder session gets (the `bind-agent`
skill). Code does the rest through the workbench, exactly as `run --drafts` does: one retry on a
refusal and one on a failing proof. The pairing the person reviewed is frozen first, and every
binding the model writes is held to it in code. A scenario whose proof still fails after the
retry fails the adaptation: only a scenario no route of which the binding reaches is unscorable,
and the proof decides that, not this module. The person chose run mode, so the approval is given.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any

from ..releases import BindingMode
from .errors import AdaptationFailure
from .workbench import Workbench

STAGE = "adaptation"
_SKILL = "bind-agent"
# Where a profile carries the agent's own instructions (`workbench._agent_summary`).
_INSTRUCTIONS = (
    "general_prompt",
    "instructions",
    "begin_message",
    "first_message",
    "states",
    "conversation_flow",
)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


async def _yes(_question: str, _options: list[str]) -> str:
    return "yes"


def _spec() -> str:
    """The skill's mapping language and how a binding is proved, as the builder reads them."""
    from ..config import load_skill

    skill = load_skill(_SKILL, preamble=False)
    return skill[skill.index("## The mapping language") :]


def _binding_prompt(workbench: Workbench, pairing: Any) -> list[dict[str, str]]:
    paired = (
        "\n\nTHE PERSON'S PAIRING (environment tool -> agent tool, null where the agent has "
        "none):\n" + json.dumps(pairing, indent=1)
        if isinstance(pairing, Mapping) and pairing
        else ""
    )
    return [
        {
            "role": "system",
            "content": "You bind a customer's agent to a prebuilt environment it was not built "
            "for: the declarative mapping that turns each call the agent makes, in its own "
            "vocabulary, into calls on the environment's tools. Code checks and proves what you "
            "write; nobody else will repair it.\n\n" + _spec(),
        },
        {
            "role": "user",
            "content": workbench.brief()
            + paired
            + "\n\nWrite the binding in one answer.\n"
            "- `binding`: {tools, fixtures, aliases, rendering}, every admitted agent tool "
            "exactly once. Keep the person's pairing exactly: an agent tool paired with an "
            "environment tool maps onto that tool (an earlier step may look up a value it "
            "needs), and an agent tool the pairing does not name is {agent_tool, unsupported: "
            "<reason>}.\n"
            "- `trajectories`: {scenario_key: [{tool, arguments}]} only for scenarios whose "
            "reference solution the binding does not simply invert; code derives the rest.\n"
            "- `conflicts`: [{rule, agent}] for every agent_policy rule the agent's own "
            "instructions contradict or ignore: `rule` is the policy rule id and `agent` quotes "
            "the agent's instructions exactly. An empty list when there are none.\n"
            'Answer with one JSON object and nothing else: {"binding": {...}, '
            '"trajectories": {...}, "conflicts": [...]}.',
        },
    ]


def _conflicts_prompt(workbench: Workbench) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": workbench.brief()
            + "\n\nList every agent_policy rule in the environment policy that the agent's own "
            "instructions contradict or ignore: `rule` is the policy rule id and `agent` quotes "
            "the agent's instructions exactly. Do not guess. Answer with one JSON object and "
            'nothing else: {"conflicts": [{"rule": "...", "agent": "..."}]} (an empty list when '
            "there are none).",
        }
    ]


async def _complete(messages: list[dict[str, str]]) -> str:
    """One chat completion through Agent Command Center's OpenAI-compatible API."""
    from openai import AsyncOpenAI

    from fi import agentcc

    from ..config import chosen_model, refuse_a_model_we_cannot_afford
    from ..session import record_turn

    configured = agentcc.gateway()
    if configured is None:
        raise AdaptationFailure(
            "adaptation_model_unavailable", "AGENTCC_API_KEY is not set"
        )
    model = chosen_model()
    refuse_a_model_we_cannot_afford(model)
    client = AsyncOpenAI(
        base_url=configured.openai_base_url, api_key=configured.api_key
    )
    response = await client.chat.completions.create(
        # The gateway names a Gemini model by the provider serving it, as the harness's own
        # Gemini sessions do (`backends/vertex_gemini.py`).
        model=agentcc.vertex_model(model) if "gemini" in model.lower() else model,
        messages=messages,
        temperature=0,
        response_format={"type": "json_object"},
    )
    if response.usage is not None:
        record_turn(
            STAGE,
            model,
            response.usage.prompt_tokens or 0,
            response.usage.completion_tokens or 0,
        )
    return response.choices[0].message.content or ""


async def _ask(
    messages: list[dict[str, str]], said: str | None = None
) -> dict[str, Any]:
    """The model's next answer in this conversation, parsed; `said` is code's reply to the last."""
    if said is not None:
        messages.append({"role": "user", "content": said})
    try:
        text = await _complete(messages)
    except AdaptationFailure:
        raise
    except Exception as exc:  # the model call failing ends the run, with why
        raise AdaptationFailure(
            "adaptation_model_failed", f"the model call failed: {exc}"
        ) from exc
    messages.append({"role": "assistant", "content": text})
    try:
        body = json.loads(_FENCE.sub("", text.strip()))
    except ValueError as exc:
        raise AdaptationFailure(
            "adaptation_model_failed", f"the model's answer is not JSON: {exc}"
        ) from exc
    if not isinstance(body, dict):
        raise AdaptationFailure(
            "adaptation_model_failed", "the model's answer is not a JSON object"
        )
    return body


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        value = list(value.values())
    if isinstance(value, list):
        return [text for one in value for text in _strings(one)]
    return []


def _normal(text: str) -> str:
    return " ".join(text.split()).lower()


def _quoted(workbench: Workbench, items: Any) -> list[dict[str, str]]:
    """The conflicts that name a policy rule and quote the agent's instructions; the rest are
    dropped rather than failing the run."""
    rules = {rule.id for rule in workbench.policy.rules}
    target = workbench.profile.target
    said = _normal(" ".join(_strings([target.get(key) for key in _INSTRUCTIONS])))
    kept: list[dict[str, str]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        rule, agent = str(item.get("rule") or ""), str(item.get("agent") or "").strip()
        if rule in rules and agent and _normal(agent) in said:
            kept.append({"rule": rule, "agent": agent})
    return kept


def _failed(said: str) -> str:
    return "\n".join(line for line in said.splitlines() if ": failed" in line)


async def adapt(workbench: Workbench, pairing: Any = None) -> None:
    if workbench.mode is not BindingMode.TOOL_ROUTED:
        try:
            answer = await _ask(_conflicts_prompt(workbench))
        except AdaptationFailure:
            answer = {}
        workbench.report_conflicts(_quoted(workbench, answer.get("conflicts")))
        return await _finish(workbench)
    if pairing is None:
        raise AdaptationFailure(
            "adaptation_pairing_missing",
            "run mode binds the pairing the person reviewed, and the job carries none",
        )
    # Frozen and complete before the model is asked: the prompt shows what code will enforce.
    workbench.freeze_pairing(pairing)
    messages = _binding_prompt(workbench, workbench.pairing)
    answer = await _ask(messages)
    accepted, said = workbench.submit_binding(answer)
    if not accepted:
        answer = await _ask(
            messages,
            f"submit_binding: not accepted:\n{said}\nFix it and answer again with the whole "
            "JSON object.",
        )
        accepted, said = workbench.submit_binding(answer)
        if not accepted:
            return workbench.fail("binding_invalid", said)
    passed, said = await asyncio.to_thread(workbench.run_proof)
    if not passed:
        try:
            retry = await _ask(
                messages,
                f"run_proof: these scenarios failed:\n{_failed(said) or said}\nRepair the "
                "mapping or the trajectories (never bend a mapping to make a scenario pass) "
                "and answer again with the whole JSON object.",
            )
        except AdaptationFailure:
            retry = {}
        if retry and workbench.submit_binding(retry)[0]:
            answer = retry
            passed, said = await asyncio.to_thread(workbench.run_proof)
    if not passed:
        # A failed proof is the mapping or a trajectory not achieving the scenario, never a
        # scenario the agent cannot be measured on: nothing is sealed.
        return workbench.fail(
            "binding_unproved", f"the binding is not proved:\n{_failed(said) or said}"
        )
    workbench.report_conflicts(_quoted(workbench, answer.get("conflicts")))
    await _finish(workbench)


async def _finish(workbench: Workbench) -> None:
    finished, said = await workbench.finish(_yes)
    if not finished:
        workbench.fail(workbench.stop_code(), said)
