"""Turn a generated scenario into the inputs a live voice simulation needs.

The simulator's system prompt is already built by ``fi.simulate.simulation.voice_prompt``: it
composes identity, situation, objective, personality and the voice execution rules, and leaves one
slot open for extra instruction. Nothing here re-implements that. The generated scenario supplies
what the template was always missing:

- ``agent_input`` is the situation instruction, and lands in ``Persona.situation``;
- ``expected_outcome.world_state`` is what the caller is trying to reach, and lands in
  ``Persona.outcome``;
- ``facts`` carry disclosure rules, which no persona template can express on its own, and are
  rendered into the additional-instruction slot so the simulated caller knows what it may volunteer
  and what it must wait to be asked for.

That last part is what makes a generated scenario testable rather than merely playable: a scenario
about eliciting a drink choice only means something if the caller withholds it until asked.
"""

from __future__ import annotations

from typing import Any, Mapping

from fi.simulate.simulation.models import Persona, PersonaFact
from fi.simulate.simulation.voice_prompt import build_voice_simulator_prompt

_DISCLOSURE_RULES = {
    "volunteer": "Say this early, without being asked.",
    "on_request": "Do NOT say this until the agent asks for it. If the agent never asks, never say it.",
    "withhold": "Never reveal this, whatever the agent asks.",
}


def persona_from_record(record: Mapping[str, Any]) -> Persona:
    """The generated scenario as the simulator's persona."""
    persona_payload = dict(record.get("persona") or {})
    persona_payload.setdefault("name", "Caller")
    persona_payload.setdefault("role", "customer")
    outcome = record.get("expected_outcome") or {}
    facts = [
        PersonaFact(
            key=str(fact["key"]),
            value=str(fact.get("value", "")),
            disclosure=str(fact.get("disclosure", "on_request")),
        )
        for fact in record.get("facts") or []
        if isinstance(fact, Mapping) and fact.get("key")
    ]
    return Persona(
        persona=persona_payload,
        situation=str(record.get("agent_input", "")),
        outcome=str(outcome.get("world_state") or record.get("goal", "")),
        knowledge=facts,
    )


def disclosure_instructions(record: Mapping[str, Any]) -> str:
    """The caller's facts, grouped by when they are allowed to say them.

    Written as instructions to the person, not as a data structure, because the simulator reads
    this as part of its own character rather than as configuration.
    """
    facts = [
        f for f in record.get("facts") or [] if isinstance(f, Mapping) and f.get("key")
    ]
    if not facts:
        return ""
    lines = [
        "Your objective describes where you end up, not a shortcut for getting there. Play your "
        "situation one step at a time, in the order it describes. Where it says you ask for "
        "something and then change your mind, you must first ask for that thing and see it "
        "confirmed as part of your order, answering any questions needed to complete it, before "
        "you mention changing anything. Deciding against it while it is still being set up is the "
        "one thing that ruins this call, because the part being tested never happens.",
        "",
        "You know the following things. When you may say each one is part of who you are in this "
        "call, and getting it wrong changes what is being tested.",
    ]
    for disclosure, rule in _DISCLOSURE_RULES.items():
        group = [
            f for f in facts if str(f.get("disclosure", "on_request")) == disclosure
        ]
        if not group:
            continue
        lines.append("")
        lines.append(rule)
        for fact in group:
            label = str(fact["key"]).replace("_", " ")
            lines.append(f"- {label}: {fact.get('value', '')}")
    turns = record.get("max_reasonable_turns")
    if isinstance(turns, int) and turns > 0:
        lines.append("")
        lines.append(
            f"A competent agent can finish this in about {turns} of your turns. If the call runs "
            "far past that without progress, wind it up rather than continuing indefinitely."
        )
    return "\n".join(lines)


def simulator_prompt(
    record: Mapping[str, Any],
    *,
    call_type: str = "inbound",
    agent_name: str | None = None,
    default_language: str | None = None,
) -> str:
    """The finished system prompt for the simulated caller in one generated scenario."""
    return build_voice_simulator_prompt(
        persona_from_record(record),
        call_type=call_type,  # type: ignore[arg-type]
        agent_name=agent_name,
        additional_instructions=disclosure_instructions(record),
        default_language=default_language,
    )
