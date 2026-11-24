"""Tool-call redundancy uses tool calls, not trajectory steps, as its denominator."""

import pytest

from fi.evals.metrics.agents import (
    AgentStep,
    AgentTrajectoryInput,
    StepEfficiency,
    TaskDefinition,
    ToolCall,
    TrajectoryScore,
)


class TestStepEfficiency:
    def test_redundant_calls_within_single_step(self):
        """Multiple redundant calls in one step must not push the score below 0."""
        metric = StepEfficiency()
        step = AgentStep(
            step_number=1,
            thought="Repeating the same call",
            tool_calls=[
                ToolCall(
                    name="search",
                    arguments={"query": "test"},
                    result="result",
                    success=True,
                )
                for _ in range(5)
            ],
            is_final=True,
        )
        input_data = AgentTrajectoryInput(
            trajectory=[step], task=TaskDefinition(description="Search for something")
        )
        result = metric.compute_one(input_data)
        assert 0.0 <= result["output"] <= 1.0
        assert result["details"]["redundant_steps"] == 4


def repeated_trajectory(call_counts, failed=0):
    steps = []
    for i, count in enumerate(call_counts):
        steps.append(
            AgentStep(
                step_number=i + 1,
                tool_calls=[
                    ToolCall(
                        name="search",
                        arguments={"query": "synthetic"},
                        success=j >= failed,
                    )
                    for j in range(count)
                ],
                is_final=i == len(call_counts) - 1,
            )
        )
    return AgentTrajectoryInput(
        trajectory=steps,
        task=TaskDefinition(description="Find the synthetic result"),
        final_result="done",
        expected_result="done",
    )


@pytest.mark.parametrize("call_counts", [[5], [3, 2], [0, 2, 0, 0]])
@pytest.mark.parametrize("weights", [(0.4, 0.3, 0.3), (0.2, 0.6, 0.2)])
def test_exact_per_call_redundancy_score(call_counts, weights):
    step_weight, redundancy_weight, failure_weight = weights
    metric = StepEfficiency(
        {
            "expected_step_weight": step_weight,
            "redundancy_weight": redundancy_weight,
            "failure_weight": failure_weight,
        }
    )
    result = metric.compute_one(repeated_trajectory(call_counts))
    calls = sum(call_counts)
    expected = round(step_weight + redundancy_weight / calls + failure_weight, 4)
    assert result["output"] == pytest.approx(expected)
    assert result["details"]["redundant_steps"] == calls - 1
    assert result["details"]["total_steps"] == len(call_counts)
    assert 0 <= result["output"] <= 1


@pytest.mark.parametrize("failed", [1, 5])
def test_failed_calls_keep_separate_per_call_penalty(failed):
    result = StepEfficiency().compute_one(repeated_trajectory([5], failed=failed))
    expected = round(0.4 + 0.3 / 5 + 0.3 * (1 - failed / 5), 4)
    assert result["output"] == pytest.approx(expected)
    assert result["details"]["failed_calls"] == failed
    assert result["details"]["redundant_steps"] == 4


@pytest.mark.parametrize("weights", [(0.4, 0.3, 0.3), (0.2, 0.6, 0.2)])
def test_trajectory_score_uses_corrected_efficiency(weights):
    completion, efficiency, tool = weights
    result = TrajectoryScore(
        {
            "completion_weight": completion,
            "efficiency_weight": efficiency,
            "tool_weight": tool,
        }
    ).compute_one(repeated_trajectory([5]))
    assert result["component_scores"]["step_efficiency"] == pytest.approx(0.76)
    assert result["output"] == pytest.approx(
        round(0.9 * completion + 0.76 * efficiency + tool, 4)
    )


@pytest.mark.parametrize(
    "call_counts, expected, redundant",
    [([], 0, None), ([0, 0], 1, 0), ([1], 1, 0), ([1, 1, 1, 1, 1], 0.76, 4)],
)
def test_compatibility_controls(call_counts, expected, redundant):
    result = StepEfficiency().compute_one(repeated_trajectory(call_counts))
    assert result["output"] == pytest.approx(expected)
    if redundant is not None:
        assert result["details"]["redundant_steps"] == redundant
