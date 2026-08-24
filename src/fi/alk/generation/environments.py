"""The environments this harness can generate runnable scenarios for.

An agent may be reachable several ways at once: the same ordering assistant can take a phone call,
a web chat, or a browser session. Which one a suite targets is therefore a choice the operator
makes, not a property the harness can read off the source, and it is passed in explicitly.

The set is closed on purpose. A scenario is only worth generating if the runtime can actually stage
it and grade it, so an environment appears here once `fi.simulate` carries a plugin for it. Asking
for anything else raises rather than falling back on a generic shape, because a generic shape yields
scenarios that look correct in a report and cannot be run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvironmentProfile:
    """Everything that changes about generation when the target environment changes."""

    key: str
    label: str
    alk_plugin: str  # the fi.simulate environment plugin a scenario binds to
    conversational: bool  # a simulated user drives the interaction
    input_spec: str  # what the scenario's agent_input field must contain
    witnessable: tuple[str, ...]  # checkpoint kinds this environment can express
    gradable_today: tuple[str, ...]  # of those, the kinds the runtime already grades
    mock_surface: str  # how a tool call is intercepted here
    compatible_modalities: tuple[str, ...]  # advisory cross-check against the contract

    def pending_kinds(self) -> tuple[str, ...]:
        return tuple(k for k in self.witnessable if k not in self.gradable_today)


VOICE = EnvironmentProfile(
    key="voice",
    label="voice call",
    alk_plugin="voice",
    conversational=True,
    input_spec=(
        "a situation instruction for the simulated caller, written in second person as the "
        "caller's own lived circumstance: who they are, what is happening, and what they want. It "
        "describes their experience and goal, never instructions about what to say, and never the "
        "other side's turns. Facts the agent is expected to ask for live in `facts`, not here. No "
        "accent or voice notes"
    ),
    witnessable=("tool_call_args", "state", "conveyed", "absent", "judge"),
    # A live call is graded from the provider's post-call evidence and the transcript. World state
    # is expressed by the scenario and asserted once a tool-mocking environment is attached to the
    # voice leg, which is the runtime lane's work.
    gradable_today=("tool_call_args", "conveyed", "absent", "judge"),
    mock_surface=(
        "tool calls are answered by the scenario's mock_responses, and the values the agent passed "
        "are recovered from the call's recorded tool events"
    ),
    compatible_modalities=("voice",),
)

CHAT = EnvironmentProfile(
    key="chat",
    label="text chat",
    alk_plugin="chat",
    conversational=True,
    input_spec=(
        "a situation instruction for the simulated user, second person, their lived circumstance: "
        "their goal and what they already know. Facts the agent is meant to elicit are listed "
        "separately in `facts`, not here"
    ),
    witnessable=("tool_call_args", "state", "conveyed", "absent", "judge"),
    gradable_today=("tool_call_args", "state", "conveyed", "absent", "judge"),
    mock_surface=(
        "every tool call is served by the scenario's mock_responses and its state_updates are "
        "applied to the world, so both the arguments and the resulting state are observable"
    ),
    compatible_modalities=("chat", "data_sql", "research", "other"),
)

SUPPORTED: dict[str, EnvironmentProfile] = {VOICE.key: VOICE, CHAT.key: CHAT}

# Environments the model may report from the source but that no runtime can stage yet. Named
# separately so the error explains the gap instead of only listing what works.
_NOT_YET_BUILT = {
    "browser": "no browser environment plugin exists yet; a scenario would have nothing to drive",
    "computer_use": "no desktop environment plugin exists yet",
    "code": "no repository or container environment plugin exists yet",
}


def resolve(key: str) -> EnvironmentProfile:
    """Return the profile for an environment key, or explain why it cannot be generated for."""
    normalized = str(key or "").strip().lower()
    if normalized in SUPPORTED:
        return SUPPORTED[normalized]
    supported = ", ".join(sorted(SUPPORTED))
    if normalized in _NOT_YET_BUILT:
        raise NotImplementedError(
            f"environment {normalized!r} is not supported: {_NOT_YET_BUILT[normalized]}. "
            f"Supported today: {supported}."
        )
    raise NotImplementedError(
        f"unknown environment {normalized!r}. Supported today: {supported}."
    )


def modality_mismatch(profile: EnvironmentProfile, modality: str) -> str:
    """Advisory only: the operator's choice always wins, but a mismatch is worth saying out loud."""
    found = str(modality or "").strip().lower()
    if not found or found in profile.compatible_modalities:
        return ""
    return (
        f"the agent's source reads as a {found!r} agent, but scenarios are being generated for the "
        f"{profile.key!r} environment; the generated input shape will follow {profile.key!r}"
    )
