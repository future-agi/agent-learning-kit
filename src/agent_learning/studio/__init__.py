"""Persona & Scenario Studio — the Phase-7 common surface (ARCH §2a).

ONE studio package: re-exports the evolved ``fi.simulate`` engine classes
lazily (one class, one home — no parallel format) plus the studio API
(create / validate / calibrate / admit / lint / expand / coverage / import /
pull). The dependency points one way (facade -> ``fi.*``): class layers,
the behavior-policy compiler, realization metrics, and fidelity math live
ENGINE-side; this package never imports ``agent_learning.live`` (the
live_lane_boundary rule).
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    # evolved engine classes (lazy — the redteam.py _manifest()/_simulate() idiom)
    "Persona": ("fi.simulate.simulation.models", "Persona"),
    "Scenario": ("fi.simulate.simulation.models", "Scenario"),
    "PersonaIdentity": ("fi.simulate.simulation.models", "PersonaIdentity"),
    "PersonaTemperament": ("fi.simulate.simulation.models", "PersonaTemperament"),
    "BehaviorPolicy": ("fi.simulate.simulation.models", "BehaviorPolicy"),
    "PersonaFact": ("fi.simulate.simulation.models", "PersonaFact"),
    "AttackConditioning": ("fi.simulate.simulation.models", "AttackConditioning"),
    "PersonaProvenance": ("fi.simulate.simulation.models", "PersonaProvenance"),
    "EscalationArc": ("fi.simulate.simulation.models", "EscalationArc"),
    # engine fidelity facades
    "persona_fidelity": ("fi.simulate.simulation.fidelity", "persona_fidelity"),
    "attach_fidelity": ("fi.simulate.simulation.fidelity", "attach_fidelity"),
    # in-character fidelity as attack quality (unit 8)
    "attack_quality": ("agent_learning.studio._fidelity_attack", "attack_quality"),
    "persona_conditioned_campaign": (
        "agent_learning.studio._fidelity_attack",
        "persona_conditioned_campaign",
    ),
    # studio API
    "build_persona": ("agent_learning.studio._calibration", "build_persona"),
    "validate_persona": ("agent_learning.studio._calibration", "validate_persona"),
    "calibrate_persona": ("agent_learning.studio._calibration", "calibrate_persona"),
    "upgrade_legacy_persona": ("agent_learning.studio._upgrade", "upgrade_legacy_persona"),
    "expand_scenarios": ("agent_learning.studio._coverage", "expand_scenarios"),
    "synthesize_next_scenario": ("agent_learning.studio._coverage", "synthesize_next_scenario"),
    "coverage_report": ("agent_learning.studio._coverage", "coverage_report"),
    "residual_uncovered_estimate": ("agent_learning.studio._coverage", "residual_uncovered_estimate"),
    "bias_lint": ("agent_learning.studio._bias", "bias_lint"),
    "import_vendor_persona": ("agent_learning.studio._vendor", "import_vendor_persona"),
    "render_vendor_text": ("agent_learning.studio._vendor", "render_vendor_text"),
    "pull_personas": ("agent_learning.studio._download", "pull_personas"),
    "pull_scenarios": ("agent_learning.studio._download", "pull_scenarios"),
    "load_persona": ("agent_learning.studio._library", "load_persona"),
    "save_persona": ("agent_learning.studio._library", "save_persona"),
    "load_scenario": ("agent_learning.studio._library", "load_scenario"),
    "save_scenario": ("agent_learning.studio._library", "save_scenario"),
}


def __getattr__(name: str) -> Any:
    if name in _EXPORTS:
        module_name, attribute = _EXPORTS[name]
        value = getattr(importlib.import_module(module_name), attribute)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})


__all__ = sorted(_EXPORTS)
