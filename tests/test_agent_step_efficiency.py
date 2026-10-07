"""StepEfficiency score-bounds regression tests (agent-learning-kit#60).

The redundancy term counted duplicate *tool calls* but divided by the
number of *steps*, so a step holding several redundant calls pushed the
ratio below zero and the documented 0.0-1.0 score went negative. The fix
divides by total_calls, matching the failure term's denominator.
"""

from fi.evals.metrics.agents.metrics import StepEfficiency, TrajectoryScore
from fi.evals.metrics.agents.types import (
    AgentStep,
    AgentTrajectoryInput,
    TaskDefinition,
    ToolCall,
)


def _calls(name: str, args: dict, n: int) -> list:
    return [ToolCall(name=name, arguments=args) for _ in range(n)]


def _score(metric, trajectory, **kwargs):
    return metric.compute_one(
        AgentTrajectoryInput(
            trajectory=trajectory,
            task=TaskDefinition(description="do the thing"),
            **kwargs,
        )
    )


class TestStepEfficiencyBounds:
    def test_multiple_redundant_calls_in_one_step_stays_in_range(self):
        """The reported repro: 5 identical calls in a single step."""
        step = AgentStep(
            step_number=1,
            tool_calls=_calls("search", {"q": "x"}, 5),
            is_final=True,
        )
        result = _score(StepEfficiency(), [step])
        # 4 of 5 calls redundant -> ratio 0.2 -> 0.4 + 0.3*0.2 + 0.3
        assert result["output"] == 0.76
        assert 0.0 <= result["output"] <= 1.0
        assert result["details"]["redundant_calls"] == 4

    def test_single_redundant_call_per_step(self):
        """One call per step, repeated across steps — pre-existing case."""
        trajectory = [
            AgentStep(
                step_number=i,
                tool_calls=_calls("search", {"q": "x"}, 1),
                is_final=(i == 3),
            )
            for i in range(1, 4)
        ]
        result = _score(StepEfficiency(), trajectory)
        # 2 of 3 calls redundant -> ratio 1/3 -> 0.4 + 0.1 + 0.3
        assert result["output"] == 0.8

    def test_no_redundant_calls_scores_full_redundancy_weight(self):
        trajectory = [
            AgentStep(
                step_number=1,
                tool_calls=[
                    ToolCall(name="search", arguments={"q": "a"}),
                    ToolCall(name="fetch", arguments={"u": "b"}),
                ],
                is_final=True,
            )
        ]
        result = _score(StepEfficiency(), trajectory)
        assert result["output"] == 1.0
        assert result["details"]["redundant_calls"] == 0

    def test_worst_case_all_redundant_never_negative(self):
        """Every call after the first redundant: score must bottom at ~0
        for the redundancy term, not below."""
        step = AgentStep(
            step_number=1,
            tool_calls=_calls("search", {"q": "x"}, 20),
            is_final=True,
        )
        result = _score(StepEfficiency(), [step])
        assert result["output"] >= 0.0
        # 19/20 redundant -> ratio 0.05 -> 0.4 + 0.015 + 0.3
        assert result["output"] == 0.715

    def test_zero_tool_calls_no_division_error(self):
        step = AgentStep(step_number=1, tool_calls=[], is_final=True)
        result = _score(StepEfficiency(), [step])
        assert 0.0 <= result["output"] <= 1.0
        assert result["details"]["redundant_calls"] == 0

    def test_composed_trajectory_score_stays_in_range(self):
        """TrajectoryScore weights StepEfficiency at 30% — a negative
        efficiency pulled the composite below zero too."""
        step = AgentStep(
            step_number=1,
            tool_calls=_calls("search", {"q": "x"}, 5),
            is_final=True,
        )
        result = _score(TrajectoryScore(), [step])
        assert 0.0 <= result["output"] <= 1.0
