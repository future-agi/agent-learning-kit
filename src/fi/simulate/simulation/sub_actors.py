"""How a sub-actor can reach the agent under test, and when one enters.

Shared by the build, which refuses a sub-actor no run could deliver, and by every runner. Each
channel names the agent types it reaches and whether a runner can deliver it yet, so a new agent
type adds a channel here instead of a branch in the code that reads it.
"""

from __future__ import annotations

from typing import Any

CHANNELS: dict[str, dict[str, Any]] = {
    "audio_mix": {
        "modalities": {"voice"},
        "available": True,
        "about": "a voice near the person that speaks up whenever it has something to say, which the agent may hear",
        "setting": "You are near someone who is on a phone call with an automated agent. You are not on "
        "the call yourself; the agent may still hear you.",
    },
    "audio_handoff": {
        "modalities": {"voice"},
        "available": True,
        "about": "someone who takes the phone from the person for a few turns, then hands it back",
        "setting": "You have just taken the phone from the person who was talking to an automated agent.",
    },
    "ivr": {
        "modalities": {"voice"},
        "available": True,
        "about": "a phone menu on the person's own line that answers a call the agent places, before the person, "
        "and puts the call through to them",
        "setting": "You are the automated phone menu that answers this line before a person does.",
    },
    "thread_message": {
        "modalities": {"chat"},
        "available": True,
        "about": "a second participant in the chat, seen by the agent and the person",
        "setting": "You are a second person in a chat between someone and an automated agent.",
    },
    "ui_event": {
        "modalities": {"computer_use"},
        "available": False,
        "about": "something on the screen",
    },
    "page_content": {
        "modalities": {"browser"},
        "available": False,
        "about": "content on a page",
    },
    "repo_change": {
        "modalities": {"coding"},
        "available": False,
        "about": "a change in the repository",
    },
    "peer_agent": {
        "modalities": {"multi_agent"},
        "available": False,
        "about": "a message from another agent",
    },
}

TRIGGER_ABOUT: dict[str, str] = {
    "always": "after the agent's first turn (a phone menu answers the call itself)",
    "mid_journey": "after the agent's turn `turn`",
    "on_tool": "on call `on_call` of the agent's tool `tool`",
    "on_keyword": "when one of `words` is in the agent's latest turn",
    "probability": "with chance `p`, drawn once per call from the run's seed",
}
TRIGGERS = tuple(TRIGGER_ABOUT)


def available_channels(modality: str = "") -> list[str]:
    """The channels a runner can deliver, to an agent of this kind when one is named."""
    return [
        name
        for name, entry in CHANNELS.items()
        if entry["available"] and (not modality or modality in entry["modalities"])
    ]


def described(entries: dict[str, str]) -> str:
    return "; ".join(f"`{name}`: {about}" for name, about in entries.items())
