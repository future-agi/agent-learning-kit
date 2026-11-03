"""What each scenario needs: derived from its content, never estimated."""

from fi.alk.harness.scenario_requirements import check_reads, required_tools, scenario_requirements

_WORLD_CHECK = "def check(world, calls):\n    return None if world.state('refunds') else 'no refund'\n"
_CALLS_CHECK = "def check(w, c):\n    return None if any(x.name == 'refund' for x in c) else 'not called'\n"
_NEITHER = "def check(world, calls):\n    return None\n"


def test_a_tool_one_correct_path_avoids_is_not_required():
    scenario = {
        "solution": [{"tool": "lookup_order"}, {"tool": "refund"}],
        "alternatives": [{"solution": [{"tool": "lookup_order"}, {"tool": "transfer"}]}],
    }
    assert required_tools(scenario) == ["lookup_order"]
    assert required_tools({"solution": []}) == []


def test_which_of_world_and_calls_a_check_reads_comes_from_its_code():
    assert check_reads(_WORLD_CHECK) == {"world"}
    assert check_reads(_CALLS_CHECK) == {"calls"}
    assert check_reads(_NEITHER) == set()
    assert check_reads("not python (") == set()


def test_each_scenario_lists_what_it_needs():
    scenarios = [
        {
            "scenario_key": "refund",
            "max_turns": 8,
            "solution": [{"tool": "refund"}],
            "sub_goals": ["refund_recorded", "refund_called", "polite"],
        },
        {"scenario_key": "greeting", "max_turns": 1, "solution": [], "sub_goals": ["polite"]},
    ]
    checks = {"refund_recorded": _WORLD_CHECK, "refund_called": _CALLS_CHECK, "polite": _NEITHER}
    result = scenario_requirements(
        scenarios, {"modality": "voice", "conversational": True}, checks, seeded_world=True, selection_size=2
    )["scenarios"]
    assert result["refund"] == {
        "capabilities": [
            "audio_evidence",
            "multi_turn",
            "one_turn",
            "start_health",
            "state_evidence",
            "tool_discovery",
            "tool_routing",
            "world_reset",
        ],
        "tools": {"required": ["refund"], "any_of": []},
        "sub_goal_evidence": {"world": ["refund_recorded"], "calls": ["refund_called"]},
    }
    assert result["greeting"]["capabilities"] == ["audio_evidence", "one_turn", "start_health", "world_reset"]


def test_one_scenario_alone_does_not_need_the_world_reset():
    alone = scenario_requirements(
        [{"scenario_key": "s", "max_turns": 1}], {"modality": "chat"}, {}, seeded_world=True, selection_size=1
    )["scenarios"]["s"]
    assert alone["capabilities"] == ["one_turn", "start_health"]
