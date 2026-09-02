"""Reading a suite that is too large to read.

The failure this exists to catch is not a bad scenario. It is a suite whose count keeps climbing
while the number of distinct things it would catch does not, which every individual scenario in it
passes all three gates without noticing.
"""

from __future__ import annotations

from fi.alk.harness.diversity import measure
from fi.alk.harness.scenario import Persona, Scenario


def one(name: str, tests: str = "", who: str = "", where: str = "", accent: str = "American"):
    return Scenario(
        name=name,
        tests=tests or f"the agent handles {name}",
        instruction="do the thing",
        persona=Persona(name=who or "Dana", location=where or "Berlin", accent=accent),
    )


class TestASuiteThatStoppedCovering:
    def test_piling_onto_one_cell_is_called_out(self):
        held = [one(f"retrieve-ride__{i}", tests=f"case number {i} of finding a ride") for i in range(18)]
        held += [one("cancel-ride__a"), one("cancel-ride__b")]
        said = " ".join(measure(held).concerns())
        assert "retrieve-ride" in said and "one thing wearing the shape of a broad one" in said

    def test_an_evenly_spread_suite_says_nothing(self):
        held = [
            one(f"cell{i}-thing__baseline", tests=f"a wholly separate matter number {i}",
                who=f"Person{i}", where=f"City{i}", accent="American" if i % 2 else "Indian")
            for i in range(20)
        ]
        assert measure(held).concerns() == []


class TestOneTestWrittenTwice:
    def test_near_identical_scenarios_in_a_cell_are_paired(self):
        held = [
            one("diagnose-fare__a", tests="caller charged twice for a single completed trip"),
            one("diagnose-fare__b", tests="caller was charged twice for one single completed trip"),
        ]
        assert [row[:2] for row in measure(held).alike] == [("diagnose-fare__a", "diagnose-fare__b")]

    def test_the_same_situation_in_two_cells_is_left_alone(self):
        held = [
            one("diagnose-fare__a", tests="caller charged twice for a single completed trip"),
            one("cancel-ride__a", tests="caller charged twice for a single completed trip"),
        ]
        assert measure(held).alike == []


class TestThePeopleInIt:
    def test_one_name_dominating_is_reported(self):
        held = [one(f"c{i}-x__baseline", who="Dana" if i < 7 else f"Other{i}") for i in range(10)]
        assert "'Dana' is 7 of 10 caller names" in " ".join(measure(held).concerns())

    def test_a_single_accent_across_a_suite_is_reported(self):
        held = [one(f"c{i}-x__baseline", who=f"P{i}", where=f"City{i}") for i in range(10)]
        said = " ".join(measure(held).concerns())
        assert "same accent" in said

    def test_a_suite_too_small_to_judge_is_not_nagged(self):
        assert measure([one("a-b__baseline"), one("c-d__baseline")]).concerns() == []
