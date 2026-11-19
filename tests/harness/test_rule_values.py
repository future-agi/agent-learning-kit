"""Values a changed rule or tool no longer states, and the case texts still stating them."""

from __future__ import annotations

from fi.alk.harness.rule_values import old_value_mentions, old_value_notes, retired_values, values_in

CONTRACT = {
    "hard_constraints": ["Refund only within 14 days of delivery.", "Never sell more than 130 items."],
    "tools": [{"name": "issue_refund", "description": "Issues a refund inside the 14-day window."}],
    "amendments": [
        "rule removed — Refund only within 30 days of delivery.: policy",
        "rule added — Refund only within 20 days of delivery.: policy",
        "tool corrected — issue_refund: window updated from 30 days to 20 days.",
        "rule removed — Refund only within 20 days of delivery.: policy",
        "rule added — Refund only within 14 days of delivery.: policy",
    ],
    # The agent's own prompt still says 30: a rule changed here overrides it.
    "system_prompt_excerpt": "Refund only within the last 30 days.",
}


def test_a_value_is_a_number_with_the_word_after_it_however_joined():
    assert values_in("past 30 days, the 30-day window, outside_30_day_window, 4.5 percent") == {
        ("30", "day"),
        ("4.5", "percent"),
    }
    assert values_in("130 items and order 1043") == {("130", "item")}


def test_every_value_a_changed_rule_used_to_state_is_retired_and_the_current_one_is_not():
    assert retired_values(CONTRACT) == {("30", "day"), ("20", "day")}


def test_texts_still_stating_an_old_value_are_named_and_others_are_not():
    texts = [
        ("a.use_case", "Refuse a refund outside the 30-day return window"),
        ("a.name", "refuse_refund_outside_20_day_window"),
        ("a.instruction", "Delivered 41 days ago; within 14 days; at most 130 items"),
    ]
    assert old_value_mentions(CONTRACT, texts) == [("a.use_case", "30 day"), ("a.name", "20 day")]


def test_a_contract_with_no_changes_names_nothing():
    unchanged = {**CONTRACT, "amendments": []}
    assert old_value_notes(unchanged, [{"name": "a", "use_case": "outside the 30-day window"}], []) == []


def test_a_note_says_what_the_rules_say_now():
    notes = old_value_notes(CONTRACT, [{"name": "a", "use_case": "outside the 30-day window"}], [])
    assert notes == ["a.use_case still says 30 day, which a changed rule or tool no longer states (it now says 14 day)"]


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
