"""Which way a call goes changes who speaks first and who the person on the line is.

An agent that dials out is not tested by somebody who answers already knowing why the phone
rang. The customer this matters for runs agents that call people to collect information, and
every scenario written before this existed gave the person an errand of their own.
"""

from __future__ import annotations

from fi.alk.harness.contract import AgentContract
from fi.alk.harness.scenariogen.model.scenario import Scenario
from fi.alk.harness.simulator_voice import (
    OUTBOUND_INSTRUCTIONS,
    SIMULATOR_INSTRUCTIONS,
    simulator_definition,
)


class TestDirectionIsAPropertyOfTheAgent:
    def test_a_contract_is_inbound_unless_it_says_otherwise(self):
        """Every contract written before this field described an agent that was rung."""
        assert AgentContract(agent="a").direction == "inbound"

    def test_a_contract_can_say_it_dials_out(self):
        assert AgentContract(agent="a", direction="outbound").direction == "outbound"

    def test_a_scenario_inherits_the_same_default(self):
        assert Scenario(name="x").direction == "inbound"
        assert Scenario(name="x").agent_speaks_first is True

    def test_an_outbound_scenario_does_not_expect_the_agent_to_open(self):
        assert Scenario(name="x", direction="outbound").agent_speaks_first is False


class TestWhatTheSimulatedPersonIsTold:
    def build(self, direction: str):
        return simulator_definition(
            lambda _name: "", {"name": "Priya"}, direction=direction
        )

    def test_inbound_gets_the_ordinary_rules_only(self):
        said = self.build("inbound").instructions
        assert said == SIMULATOR_INSTRUCTIONS
        assert "did not place it" not in said

    def test_outbound_is_told_it_did_not_place_the_call(self):
        said = self.build("outbound").instructions
        assert said.startswith(SIMULATOR_INSTRUCTIONS)
        assert OUTBOUND_INSTRUCTIONS in said
        assert "You did not place it" in said
        assert "no errand of your own" in said


class TestTheRulesOnMakingThingsUp:
    def test_ordinary_details_may_be_invented_plausibly(self):
        assert "plausible answer that fits who you are" in SIMULATOR_INSTRUCTIONS

    def test_what_the_agent_verifies_may_not_be(self):
        """An invented code is checked, fails, and tests nothing."""
        assert "Never make one up" in SIMULATOR_INSTRUCTIONS
        assert "verification code" in SIMULATOR_INSTRUCTIONS

    def test_invented_details_must_not_read_as_placeholders(self):
        assert "1234567890" in SIMULATOR_INSTRUCTIONS
        assert "123 Main Street" in SIMULATOR_INSTRUCTIONS

    def test_surroundings_are_background_not_an_announcement(self):
        assert "background, not something to announce" in SIMULATOR_INSTRUCTIONS
