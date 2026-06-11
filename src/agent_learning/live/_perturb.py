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

PERTURBATION_OPERATORS = ("noise", "interference", "asr_error", "accent")
# Operators applicable to text-rung input (rung 1: TranscriptionFrames /
# scripted user text). Acoustic operators need a real audio channel (rung 2+).
TEXT_RUNG_OPERATORS = ("asr_error",)

_VOWELS = "aeiou"


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


def apply_text_perturbations(
    turns: Sequence[Mapping[str, Any]],
    operators: Sequence[str],
    *,
    seed: int = 0,
    asr_error_rate: float = 0.08,
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
        perturbed.append(row)
    if "asr_error" in operators:
        applied.append(
            {"operator": "asr_error", "rate": asr_error_rate, "seed": seed}
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
