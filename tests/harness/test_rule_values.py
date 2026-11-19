"""Values a changed rule or tool replaced, and the case texts still stating them."""

from __future__ import annotations

from fi.alk.harness.rule_values import changed_values, old_value_mentions, old_value_notes, retired_values, values_in

# The refund rule as it was changed on a live environment, recorded before and after.
CONTRACT = {
    "hard_constraints": ["Refund only within 14 days of delivery.", "Escalate if the customer still needs 100 dollars."],
    "tools": [{"name": "issue_refund", "description": "Issues a refund inside the 14-day window."}],
    "value_changes": [
        {"kind": "rule", "before": "Refund only within 30 days of delivery.", "after": "Refund only within 20 days of delivery."},
        {"kind": "rule", "before": "Refund only within 20 days of delivery.", "after": "Refund only within 14 days of delivery."},
    ],
    # Why each change was made: numbers here are the writer's words, never a value a rule stated.
    "amendments": [
        "rule removed — Refund only within 30 days of delivery.: 2 managers asked; the customer still needs 100 dollars",
        "rule added — Refund only within 20 days of delivery.: orders placed 30 days ago now fall outside",
    ],
    # The agent's own prompt still says 30: a rule changed here overrides it.
    "system_prompt_excerpt": "Refund only within the last 30 days.",
}

# The live environment's amendments, saved before changes were recorded before and after.
LEGACY = {
    "hard_constraints": [
        "Refund only when the customer asks, the order was delivered within the last 14 days, and list_refunds shows "
        "no earlier refund; then call issue_refund once with the full amount."
    ],
    "tools": [],
    "amendments": [
        "rule removed — Refund only when the customer asks, the order was delivered within the last 30 days, and "
        "list_refunds shows no earlier refund; then call issue_refund once with the full amount.: Return policy updated: "
        "refund window shortened from 30 days to 20 days of delivery.",
        "rule added — Refund only when the customer asks, the order was delivered within the last 20 days, and "
        "list_refunds shows no earlier refund; then call issue_refund once with the full amount.: Return policy updated: "
        "refunds are allowed only within 20 days of delivery, not 30.",
        "tool corrected — issue_refund: Refund eligibility window updated from 30 days to 20 days.",
        "rule removed — Refund only when the customer asks, the order was delivered within the last 20 days, and "
        "list_refunds shows no earlier refund; then call issue_refund once with the full amount.: Return policy updated: "
        "refund window shortened from 20 days to 14 days of delivery.",
        "rule added — Refund only when the customer asks, the order was delivered within the last 14 days, and "
        "list_refunds shows no earlier refund; then call issue_refund once with the full amount.: Return policy updated: "
        "refunds are allowed only within 14 days of delivery, not 20.",
        "tool corrected — issue_refund: Refund eligibility window updated from 20 days to 14 days.",
    ],
}


def test_a_value_is_a_number_with_the_word_after_it_however_joined():
    assert values_in("past 30 days, the 30-day window, outside_30_day_window, 4.5 percent") == {
        ("30", "day"),
        ("4.5", "percent"),
    }
    assert values_in("130 items and order 1043") == {("130", "item")}


def test_only_the_value_in_the_place_that_changed_is_retired():
    assert changed_values("Refund within 30 days, at most 2 items.", "Refund within 20 days, at most 2 items.") == {
        ("30", "day")
    }
    # A clause the change took out is not a value it replaced.
    assert changed_values("Refund within 30 days. Ask 2 managers first.", "Refund within 20 days.") == {("30", "day")}
    assert changed_values("Ask 3 managers. Refund within 30 days.", "Refund within 20 days.") == {("30", "day")}
    # Reworded around the number: the number in the same place still lines up.
    assert changed_values("Refund only within 30-day window.", "Refund only within the 20-day window.") == {
        ("30", "day")
    }


def test_retired_values_come_only_from_the_changed_rules_never_from_why_they_changed():
    assert retired_values(CONTRACT) == {("30", "day"), ("20", "day")}


def test_the_probe_s_valid_texts_are_not_flagged_and_an_old_window_is():
    texts = [
        ("a.instruction", "You still need 100 dollars before the trip."),
        ("b.instruction", "Ask whether 2 managers approved it."),
        ("c.instruction", "The order was placed 41 days ago, so the agent must refuse."),
        ("d.use_case", "Refuse a refund outside the 20-day return window"),
        ("e.name", "refuse_refund_outside_30_day_window"),
    ]
    assert old_value_mentions(CONTRACT, texts) == [("d.use_case", "20 day"), ("e.name", "30 day")]


def test_the_live_environment_s_old_windows_are_still_found_from_its_amendments():
    assert retired_values(LEGACY) == {("30", "day"), ("20", "day")}
    stale = {
        "name": "refuse_refund_outside_14_day_window",
        "use_case": "Refuse a refund request when an order is outside the 30-day return window or already refunded.",
        "negative_controls": [
            {"name": "refund_anyway", "why": "Calling issue_refund on an order past 30 days violates return policy."}
        ],
    }
    notes = old_value_notes(LEGACY, [stale], [])
    assert [one.split(" still says ")[0] for one in notes] == [
        "refuse_refund_outside_14_day_window.use_case",
        "refuse_refund_outside_14_day_window.negative_controls.refund_anyway.why",
    ]
    assert all("(it now says 14 day)" in one for one in notes)


def test_a_contract_with_no_changes_names_nothing():
    unchanged = {**CONTRACT, "value_changes": [], "amendments": []}
    assert old_value_notes(unchanged, [{"name": "a", "use_case": "outside the 30-day window"}], []) == []


def test_a_negative_control_s_why_and_a_sub_goal_are_read_too():
    scenario = {
        "name": "refuse_late",
        "negative_controls": [{"name": "refund_anyway", "why": "Calling issue_refund on an order past 30 days."}],
    }
    goal = {"name": "declined", "judged": "Pass when: the agent declines past the 20-day window."}
    notes = old_value_notes(CONTRACT, [scenario], [goal])
    assert [one.split(" still says ")[0] for one in notes] == [
        "refuse_late.negative_controls.refund_anyway.why",
        "sub-goal declined.judged",
    ]


def test_a_dropped_rule_replaced_by_one_that_reads_alike_is_recorded_before_and_after(tmp_path):
    from fi.alk.harness.amend import add_rule, drop_rule, fix_tool
    from fi.alk.harness.contract import AgentContract, ToolSpec

    contract = AgentContract(
        agent="a",
        modality="chat",
        real_use_cases=["refunds"],
        hard_constraints=["Refund only within 30 days of delivery."],
        tools=[ToolSpec(name="issue_refund", description="Refunds inside the 30-day window.")],
    )
    assert drop_rule(contract, tmp_path, rule="Refund only within 30 days of delivery.", why="policy")[0]
    # An unrelated rule added meanwhile closes nothing.
    assert add_rule(contract, tmp_path, rule="Greet the customer by name.", why="tone")[0]
    assert add_rule(contract, tmp_path, rule="Refund only within 20 days of delivery.", why="policy")[0]
    assert fix_tool(
        contract, tmp_path, tool_name="issue_refund", description="Refunds inside the 20-day window.", why="policy"
    )[0]

    assert [(one.kind, one.before, one.after) for one in contract.value_changes] == [
        ("rule", "Refund only within 30 days of delivery.", "Refund only within 20 days of delivery."),
        ("tool", "Refunds inside the 30-day window.", "Refunds inside the 20-day window."),
    ]
    assert retired_values(contract) == {("30", "day")}


def _legacy(*lines):
    return {"hard_constraints": ["Refund only within 14 days of delivery."], "tools": [], "amendments": list(lines)}


def test_a_legacy_rule_change_with_different_whys_is_still_found_and_its_whys_never_count():
    contract = _legacy(
        "rule removed — Refund only within 30 days of delivery.: 2 complaints this week: shorten the window",
        "rule added — Refund only within 14 days of delivery.: Legal asked for 14 days after 3 audits",
    )
    assert retired_values(contract) == {("30", "day")}
    texts = [
        ("a.use_case", "Refuse a refund outside the 30-day window"),
        ("b.instruction", "You have 2 complaints and 3 audits on file."),
    ]
    assert old_value_mentions(contract, texts) == [("a.use_case", "30 day")]


def test_a_legacy_removed_rule_and_an_unrelated_added_rule_are_never_paired():
    contract = _legacy(
        "rule removed — Refund only within 30 days of delivery.: policy changed",
        "rule added — Never share another customer's order details; answer a data request within 2 days of "
        "receiving it and log every request.: privacy review",
    )
    assert retired_values(contract) == set()
