"""Kit-native perturbation operators for the ``live_stressed`` sub-lane
(guide §3.6 / PRD §4.2). Imports: stdlib + numpy only.

Operators are deterministic under a recorded seed so stressed runs replay.
Flipping ANY operator stamps the run ``evidence_class="live_stressed"`` and
records the operator list in the ``live_lane.perturbations`` stanza; the run
links its clean twin (``paired_clean_run``).
"""

from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

import numpy as np

PERTURBATION_OPERATORS = (
    "noise",
    "interference",
    "asr_error",
    "accent",
    "homophone",
    "code_switch",
    "near_dup",
)
# Operators applicable to text-rung input (rung 1: TranscriptionFrames /
# scripted user text). Acoustic operators need a real audio channel (rung 2+).
TEXT_RUNG_OPERATORS = ("asr_error", "homophone", "code_switch", "near_dup")

_VOWELS = "aeiou"

# Spoken-form pairs whose transcripts diverge (rung-1 stand-in for the
# homophone-divergence surface; defensive test payloads, PRD §2 boundary).
HOMOPHONE_TABLE = {
    "to": "two", "two": "to", "for": "four", "four": "for",
    "right": "write", "write": "right", "buy": "by", "by": "buy",
    "cell": "sell", "sell": "cell", "here": "hear", "hear": "here",
    "new": "knew", "knew": "new", "wait": "weight", "weight": "wait",
    "aloud": "allowed", "allowed": "aloud", "cents": "sense", "sense": "cents",
}

# Phonologically plausible code-switch / pseudo-word substitutions around
# safety-adjacent terms (SpeechJBB 2606.06037 lineage — shipped as TEST
# payloads against the user's own agent, never as evasion guidance).
CODE_SWITCH_TABLE = {
    "password": "passwort", "account": "akaunt", "transfer": "transfèr",
    "delete": "dilit", "confirm": "konfirm", "security": "sekurité",
    "verify": "verefai", "balance": "balans", "cancel": "kansel",
}


def apply_asr_error(text: str, *, rate: float = 0.08, seed: int = 0) -> str:
    """Confusion-matrix style token corruption at a configured rate —
    deterministic under the seed. Mimics common ASR failure modes: dropped
    characters, adjacent transpositions, vowel confusions, duplications."""

    if not text or rate <= 0:
        return text
    rng = random.Random(f"{seed}:{text}")
    tokens = text.split(" ")
    corrupted: list[str] = []
    for token in tokens:
        if len(token) < 2 or rng.random() >= rate:
            corrupted.append(token)
            continue
        mode = rng.randrange(4)
        position = rng.randrange(len(token) - 1)
        if mode == 0:  # drop a character
            corrupted.append(token[:position] + token[position + 1 :])
        elif mode == 1:  # transpose adjacent characters
            corrupted.append(
                token[:position]
                + token[position + 1]
                + token[position]
                + token[position + 2 :]
            )
        elif mode == 2:  # vowel confusion
            replaced = False
            chars = list(token)
            for index, char in enumerate(chars):
                if char.lower() in _VOWELS:
                    replacement = rng.choice(_VOWELS)
                    chars[index] = (
                        replacement.upper() if char.isupper() else replacement
                    )
                    replaced = True
                    break
            corrupted.append("".join(chars) if replaced else token)
        else:  # duplicate a character
            corrupted.append(token[: position + 1] + token[position:])
    return " ".join(corrupted)


def _rewrap_token(original: str, replacement: str) -> str:
    """Re-wrap a stripped-form replacement in the original token's
    punctuation, preserving the case of the first character."""

    start = 0
    end = len(original)
    while start < end and not original[start].isalnum():
        start += 1
    while end > start and not original[end - 1].isalnum():
        end -= 1
    core = original[start:end]
    if core and core[0].isupper():
        replacement = replacement[:1].upper() + replacement[1:]
    return original[:start] + replacement + original[end:]


def apply_homophone_swap(text: str, *, rate: float = 0.15, seed: int = 0) -> str:
    """Swap table-listed tokens for their transcript-divergent twin at the
    configured rate — deterministic under the seed. Case of the first
    character is preserved; punctuation-adjacent tokens are matched on
    their stripped lowercase form and re-wrapped."""

    if not text or rate <= 0:
        return text
    rng = random.Random(f"{seed}:{text}")
    swapped: list[str] = []
    for token in text.split(" "):
        stripped = token.strip("".join(
            char for char in token if not char.isalnum()
        )) if token else token
        key = stripped.lower()
        if key not in HOMOPHONE_TABLE or rng.random() >= rate:
            swapped.append(token)
            continue
        swapped.append(_rewrap_token(token, HOMOPHONE_TABLE[key]))
    return " ".join(swapped)


def apply_code_switch(text: str, *, rate: float = 0.2, seed: int = 0) -> str:
    """Substitute safety-adjacent tokens with their code-switched /
    pseudo-word form (``CODE_SWITCH_TABLE``) at the configured rate —
    deterministic under the seed."""

    if not text or rate <= 0:
        return text
    rng = random.Random(f"{seed}:{text}")
    switched: list[str] = []
    for token in text.split(" "):
        stripped = token.strip("".join(
            char for char in token if not char.isalnum()
        )) if token else token
        key = stripped.lower()
        if key not in CODE_SWITCH_TABLE or rng.random() >= rate:
            switched.append(token)
            continue
        switched.append(_rewrap_token(token, CODE_SWITCH_TABLE[key]))
    return " ".join(switched)


def apply_near_dup(text: str, *, rate: float = 0.1, seed: int = 0) -> str:
    """Streaming-ASR doubled-hypothesis artifact: duplicate a token as an
    adjacent edit-distance-1 variant ("send" -> "send sent") at the
    configured rate; the variant reuses the ``apply_asr_error`` single-token
    corruption modes on the duplicate. Deterministic under the seed."""

    if not text or rate <= 0:
        return text
    rng = random.Random(f"{seed}:{text}")
    duplicated: list[str] = []
    for token in text.split(" "):
        duplicated.append(token)
        if len(token) < 2 or rng.random() >= rate:
            continue
        mode = rng.randrange(4)
        position = rng.randrange(len(token) - 1)
        if mode == 0:  # drop a character
            variant = token[:position] + token[position + 1 :]
        elif mode == 1:  # transpose adjacent characters
            variant = (
                token[:position]
                + token[position + 1]
                + token[position]
                + token[position + 2 :]
            )
        elif mode == 2:  # vowel confusion
            chars = list(token)
            variant = token
            for index, char in enumerate(chars):
                if char.lower() in _VOWELS:
                    replacement = rng.choice(_VOWELS)
                    chars[index] = (
                        replacement.upper() if char.isupper() else replacement
                    )
                    variant = "".join(chars)
                    break
        else:  # duplicate a character
            variant = token[: position + 1] + token[position:]
        duplicated.append(variant)
    return " ".join(duplicated)


def apply_text_perturbations(
    turns: Sequence[Mapping[str, Any]],
    operators: Sequence[str],
    *,
    seed: int = 0,
    asr_error_rate: float = 0.08,
    homophone_rate: float = 0.15,
    code_switch_rate: float = 0.2,
    near_dup_rate: float = 0.1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply text-rung operators to a user turn script. Returns the
    perturbed turns plus the applied-operator records for the
    ``live_lane.perturbations`` stanza. Acoustic operators raise — the rung
    is the gate between voice timing and voice audio evidence."""

    applied: list[dict[str, Any]] = []
    for operator in operators:
        if operator not in PERTURBATION_OPERATORS:
            raise ValueError(
                f"unknown perturbation operator {operator!r}; "
                f"expected one of {PERTURBATION_OPERATORS}"
            )
        if operator not in TEXT_RUNG_OPERATORS:
            raise ValueError(
                f"perturbation operator {operator!r} needs a real audio "
                "channel (rung 2 loopback transport or above); only "
                f"{TEXT_RUNG_OPERATORS} apply to text-rung input"
            )
    perturbed: list[dict[str, Any]] = []
    for index, turn in enumerate(turns):
        row = dict(turn)
        if "asr_error" in operators and isinstance(row.get("user"), str):
            row["user"] = apply_asr_error(
                row["user"], rate=asr_error_rate, seed=seed + index
            )
        if "homophone" in operators and isinstance(row.get("user"), str):
            row["user"] = apply_homophone_swap(
                row["user"], rate=homophone_rate, seed=seed + index
            )
        if "code_switch" in operators and isinstance(row.get("user"), str):
            row["user"] = apply_code_switch(
                row["user"], rate=code_switch_rate, seed=seed + index
            )
        if "near_dup" in operators and isinstance(row.get("user"), str):
            row["user"] = apply_near_dup(
                row["user"], rate=near_dup_rate, seed=seed + index
            )
        perturbed.append(row)
    if "asr_error" in operators:
        applied.append(
            {"operator": "asr_error", "rate": asr_error_rate, "seed": seed}
        )
    if "homophone" in operators:
        applied.append(
            {"operator": "homophone", "rate": homophone_rate, "seed": seed}
        )
    if "code_switch" in operators:
        applied.append(
            {"operator": "code_switch", "rate": code_switch_rate, "seed": seed}
        )
    if "near_dup" in operators:
        applied.append(
            {"operator": "near_dup", "rate": near_dup_rate, "seed": seed}
        )
    return perturbed, applied


def perturbations_stanza(
    applied: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    paired_clean_run: str | None = None,
) -> dict[str, Any]:
    """The ``live_lane.perturbations`` stanza (guide §3.6): operator list,
    recorded seed, and the clean-twin link (deltas render upstream)."""

    return {
        "operators": [dict(record) for record in applied],
        "seed": seed,
        "paired_clean_run": paired_clean_run,
    }


# --- acoustic operators (rung 2+ — applied to the user PCM channel before
# the framework hears it) -----------------------------------------------------


def mix_noise(
    pcm: np.ndarray, *, snr_db: float = 20.0, seed: int = 0
) -> np.ndarray:
    """Mix seeded gaussian noise into a PCM stream at the given SNR (dB)."""

    samples = np.asarray(pcm, dtype=float)
    if samples.size == 0:
        return samples
    signal_power = float((samples**2).mean())
    if signal_power == 0:
        return samples
    noise_power = signal_power / (10.0 ** (snr_db / 10.0))
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, np.sqrt(noise_power), size=samples.shape)
    return samples + noise


def mix_interference(
    pcm: np.ndarray,
    interference: np.ndarray,
    *,
    level_db: float = -10.0,
) -> np.ndarray:
    """Overlay a competing-speaker waveform at the given relative level."""

    samples = np.asarray(pcm, dtype=float)
    competing = np.asarray(interference, dtype=float)
    if samples.size == 0 or competing.size == 0:
        return samples
    if competing.size < samples.size:
        repeat_count = int(np.ceil(samples.size / competing.size))
        competing = np.tile(competing, repeat_count)
    competing = competing[: samples.size]
    signal_rms = float(np.sqrt((samples**2).mean()))
    competing_rms = float(np.sqrt((competing**2).mean()))
    if competing_rms == 0 or signal_rms == 0:
        return samples
    target_rms = signal_rms * (10.0 ** (level_db / 20.0))
    return samples + competing * (target_rms / competing_rms)
