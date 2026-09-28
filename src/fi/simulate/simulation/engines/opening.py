"""Prompt-derived opening hints and a single agent-first speech gate.

Extraction is deliberately conservative: unsupported prompt formats use normal
agent-first behavior. Greeting text is a hint, never an exact-match requirement.
"""

from __future__ import annotations

import asyncio
import re
import time


def opening_hint(prompt: str) -> str:
    section = re.search(
        r"(?:^|\n)\s*(?:#+\s*)?(?:greeting|opening(?: message)?|welcome message)\s*:\s*(.+?)(?=\n\s*#|\bInstructions\s*:|\Z)",
        prompt,
        re.I | re.S,
    )
    if section:
        return " ".join(section.group(1).split())[:1000]
    instruction = re.search(
        r"\b(?:greet (?:the |your )?(?:user|customer|caller)|(?:start|begin|open) (?:the |each )?(?:call|conversation) (?:by|with))[^\n]{0,600}",
        prompt,
        re.I,
    )
    return instruction.group(0).strip() if instruction else ""


def disclosure_only(text: str, hint: str) -> bool:
    normalized = " ".join(text.casefold().split())
    if not normalized or not hint:
        return False
    # A combined disclosure + interactive greeting must be answered. The prompt
    # can also explicitly prescribe a disclosure as the whole opening.
    if re.search(
        r"\?|\b(?:hello|hi|welcome|please|how can|how may|help you|tell me|share your)\b",
        normalized,
    ):
        return False
    if normalized.strip(" .!\"'") in hint.casefold():
        return False
    return bool(
        len(normalized.split()) <= 40
        and re.search(r"\b(call|conversation)\b", normalized)
        and re.search(
            r"\b(?:being|be|is) (?:recorded|monitored)\b|recording this", normalized
        )
    )


class PromptOpeningGate:
    def __init__(self, prompt: str, *, timeout: float = 4.0) -> None:
        self.hint = opening_hint(prompt)
        self.timeout = timeout
        self.pending = True
        self._deadline: float | None = None
        self._audio_active = False
        self._streams = 0
        self._heard_speech = False
        self._disclosure = False
        self._audio_ended_at: float | None = None
        self._changed = asyncio.Event()

    def speech_started(self) -> None:
        self._heard_speech = True
        self._audio_active = True
        self._deadline = None
        self._changed.set()

    def speech_ended(self) -> None:
        self._audio_active = False
        self._audio_ended_at = time.monotonic()
        if self._deadline is not None and self._disclosure:
            self._deadline = self._audio_ended_at + self.timeout
        self._changed.set()

    def stream_started(self) -> None:
        self._heard_speech = True
        self._streams += 1
        self._deadline = None
        self._changed.set()

    def stream_ended(self) -> None:
        self._streams = max(0, self._streams - 1)
        self._changed.set()

    def accepts(self, text: str, *, completed: bool = True) -> bool:
        if not self.pending:
            return True
        residue = self._disclosure and bool(
            re.fullmatch(
                r"(?:it|this|call|recorded|recording|for|quality|training|purposes|[\s.,!])+",
                text.casefold().strip(),
            )
        )
        suppress = not text.strip() or residue or disclosure_only(text, self.hint)
        if completed:
            self._heard_speech = True
            if suppress:
                self._disclosure = True
                # STT finalization can lag VAD. Anchor to audio end when known,
                # never add four seconds on top of transcription processing.
                self._deadline = (
                    self._audio_ended_at or time.monotonic()
                ) + self.timeout
            else:
                self.pending = False
            self._changed.set()
        return not suppress

    async def wait_then_open(self, open_conversation) -> None:
        if not self._heard_speech:
            self._deadline = time.monotonic() + self.timeout
        while self.pending:
            self._changed.clear()
            remaining = None
            if (
                self._deadline is not None
                and not self._audio_active
                and not self._streams
            ):
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    # No await between the final onset check and speech scheduling.
                    self.pending = False
                    open_conversation()
                    return
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
