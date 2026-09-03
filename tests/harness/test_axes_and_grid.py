"""The axis file and the grid derived from a contract.

These two decide what a suite can possibly cover, so the cases worth pinning are the ones where
a silent mistake would shrink the grid without failing: an object mangled into a second object, a
tool orphaned from the object it acts on, a setting that reaches nothing being counted as
coverage, and a contract too thin to derive anything at all.
"""

from __future__ import annotations

import json

import pytest

from fi.alk.harness.axes import axes_for
from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.grid import _singular, derive, object_of, objects_in


@pytest.fixture()
def axes():
    return axes_for("voice")


def contract_with(tools: list[str], schema: dict | None = None, **rest) -> AgentContract:
    return AgentContract(
        agent=rest.pop("agent", "test-agent"),
        modality=rest.pop("modality", "voice"),
        tools=[ToolSpec(name=one) for one in tools],
        data_schema=schema or {},
        **rest,
    )


class TestSingular:
    @pytest.mark.parametrize(
        "word,expected",
        [
            ("rides", "ride"),
            ("bookings", "booking"),
            ("policies", "policy"),
            # The endings that a naive trailing-s strip destroys. Each of these produced a
            # second object that duplicated a real one.
            ("address", "address"),
            ("status", "status"),
            ("sms", "sms"),
            ("analysis", "analysis"),
        ],
    )
    def test_keeps_words_whose_s_is_part_of_the_word(self, word, expected):
        assert _singular(word) == expected


class TestObjectOf:
    @pytest.mark.parametrize(
        "tool,expected",
        [
            ("book_ride", "ride"),
            ("cancel_ride", "ride"),
            ("get_payment_methods", "payment_method"),
            # A trailing qualifier says how the object was found, not what it is.
            ("lookup_rider_by_phone", "rider"),
            ("getBookingStatus", "booking_status"),
        ],
    )
    def test_reads_the_noun_out_of_a_tool_name(self, tool, expected, axes):
        verbs = {verb for operation in axes.operations for verb in operation.verbs}
        assert object_of(tool, verbs) == expected


class TestObjects:
    def test_folds_the_same_thing_named_at_different_grains(self, axes):
        contract = contract_with(
            ["get_saved_places", "geocode_address", "get_booking_status", "book_ride"],
            {"places": {}, "bookings": {}},
        )
        objects = objects_in(contract, axes)
        # saved_place is a place, booking_status is a booking. Left apart they split one
        # object's coverage across rows that each look thin.
        assert "place" in objects and "saved_place" not in objects
        assert "booking" in objects and "booking_status" not in objects


class TestDerive:
    def test_a_tool_stays_attached_to_its_object_after_folding(self, axes):
        """The bug that made a dozen state-changing tools produce five cells.

        ``send_otp`` reads as ``otp`` while the schema calls the collection ``otp_codes``. If
        folding is applied to the object list but not to the tools, the tool belongs to an
        object that no longer exists and its cell is dropped without a word.
        """
        contract = contract_with(["send_otp", "verify_otp"], {"otp_codes": {}})
        grid = derive(contract, axes)
        assert "otp_code" in grid.objects
        assert any(cell.obj == "otp_code" and cell.tools for cell in grid.cells)

    def test_reading_operations_need_no_dedicated_tool(self, axes):
        """The whole point: an agent can be asked why it charged twice without a diagnose tool."""
        contract = contract_with(["get_fares"], {"fares": {}})
        grid = derive(contract, axes)
        names = {cell.name for cell in grid.cells}
        assert {"diagnose-fare", "compare-fare", "explain-fare"} <= names

    def test_state_changing_operations_do_need_one(self, axes):
        contract = contract_with(["get_fares"], {"fares": {}})
        grid = derive(contract, axes)
        assert "cancel-fare" not in {cell.name for cell in grid.cells}
        assert "cancel-fare" in grid.dropped

    def test_conversation_operations_are_one_cell_not_one_per_object(self, axes):
        contract = contract_with(
            ["get_rides", "get_fares", "get_places"],
            {"rides": {}, "fares": {}, "places": {}},
        )
        grid = derive(contract, axes)
        manage = [cell for cell in grid.cells if cell.kind == "manage"]
        # Three conversation operations, three cells, however many objects the agent has.
        assert len(manage) == 3
        assert {cell.obj for cell in manage} == {"caller"}

    def test_an_empty_contract_still_yields_a_grid(self, axes):
        """The worst case has to proceed. A caller who asked for scenarios needs scenarios."""
        grid = derive(contract_with([], {}, agent="mystery-agent"), axes)
        assert grid.cells
        assert grid.thin
        assert "mystery-agent" not in grid.objects  # the agent name, singularised to its noun

    def test_no_operations_is_reported_rather_than_crashing(self):
        empty = axes_for("nothing-defines-this")
        grid = derive(contract_with(["get_things"]), empty)
        assert grid.cells or grid.thin


class TestAxisSet:
    def test_an_unknown_modality_falls_back_to_the_universal_axes(self):
        assert axes_for("robotics").modality == "universal"
        assert axes_for("robotics").axes

    def test_a_setting_that_reaches_nothing_is_not_offered_for_copying(self):
        voice = axes_for("voice")
        channel = voice.axis("channel")
        assert channel is not None
        copyable = {one.name for one in channel.copyable_settings(env={"ALK_BACKGROUND_NOISE": "1"})}
        # Declared for coverage accounting, but nothing in the run consumes them, so copying a
        # scenario across them would produce duplicates wearing different names.
        assert "dropping" not in copyable
        assert "interrupted" not in copyable
        assert "street" in copyable

    def test_settings_gated_on_environment_are_withheld_until_it_is_set(self):
        voice = axes_for("voice")
        assert voice.versions_per_scenario(env={}) == 9
        assert voice.versions_per_scenario(env={"ALK_BACKGROUND_NOISE": "1"}) == 12

    def test_world_backed_settings_are_authored_never_copied(self):
        voice = axes_for("voice")
        twist = voice.axis("twist")
        assert twist is not None
        assert twist.copyable_settings() == ()
        assert {one.name for one in twist.authored_settings()} == {
            "impersonation",
            "emergency",
            "fraud",
            "injection",
        }

    def test_axis_settings_only_name_persona_values_the_platform_knows(self):
        """A setting mapping to an accent nothing recognises renders and then selects no voice."""
        from fi.alk.harness.axes import unrecognised_persona_values

        for modality in ("universal", "voice"):
            assert unrecognised_persona_values(axes_for(modality)) == []


class TestPreconditionsReachTheGrid:
    """What a tool refuses until something else has happened.

    This is the one fact about an agent that no instruction to a scenario writer can replace.
    Without it a writer assumes the worst and replays the agent's whole flow to reach every cell,
    because that always works and deviating risks a refusal it cannot predict. Three rounds of
    prose guidance failed to move it; the data moves it or nothing does.
    """

    def contract(self, **rest):
        return AgentContract(
            agent="ride",
            modality="voice",
            tools=[
                ToolSpec(name="get_fares"),
                ToolSpec(name="book_ride", requires=["get_fares", "select_option"]),
            ],
            data_schema={"fares": {}, "rides": {}, "users": {}},
            **rest,
        )

    def test_a_cell_carries_what_its_tools_are_reachable_after(self, axes):
        grid = derive(self.contract(), axes)
        booking = next(one for one in grid.cells if one.name == "create-ride")
        assert booking.after == ("get_fares", "select_option")

    def test_a_cell_whose_tools_have_none_is_reachable_directly(self, axes):
        grid = derive(self.contract(), axes)
        reading = next(one for one in grid.cells if one.name == "retrieve-fare")
        assert reading.after == ()

    def test_the_description_says_which_it_is(self, axes):
        grid = derive(self.contract(), axes)
        booking = next(one for one in grid.cells if one.name == "create-ride")
        reading = next(one for one in grid.cells if one.name == "retrieve-fare")
        assert "reachable only after: get_fares" in booking.described()
        assert "reachable directly" in reading.described()

    def test_a_contract_recording_none_still_works(self, axes):
        """Older contracts predate the field. Absent data must read as unknown, not as none."""
        plain = AgentContract(
            agent="ride", modality="voice",
            tools=[ToolSpec(name="get_fares")], data_schema={"fares": {}, "users": {}, "rides": {}},
        )
        grid = derive(plain, axes)
        assert all(one.after == () for one in grid.cells)


class TestNothingIsTunedToOneAgent:
    """The universal file serves voice, chat, browser and coding agents alike.

    Voice words leaking into it is the failure mode that matters: a coding agent would be given
    cells about authenticating a caller it has never had, and guidance about how a call goes.
    Each modality supplies its own vocabulary; the skeleton supplies none.
    """

    def test_the_universal_axes_name_no_modality(self):
        import json
        from pathlib import Path

        import fi.alk.harness.axes as module

        held = json.loads((module.BUNDLED / "universal.json").read_text())
        # The note explains the rule and may quote the words it forbids; the axes may not use them.
        held.pop("notes", None)
        text = json.dumps(held).lower()
        for word in ("caller", "on this call", "phone", "accent", "spoken", "dial tone"):
            assert word not in text, f"{word!r} is voice-specific and the universal file uses it"

    def test_each_modality_names_its_own_counterparty(self):
        assert axes_for("voice").counterparty == "caller"
        assert axes_for("chat").counterparty == "person"
        assert axes_for("coding").counterparty == "person"

    def test_conversation_cells_are_named_for_it(self):
        """A coding agent must not get a cell about authenticating a caller."""
        contract = contract_with(["get_tickets", "transfer_to_human"], {"tickets": {}, "users": {}, "notes": {}})
        for modality, expected in (("voice", "authenticate-caller"), ("coding", "authenticate-person")):
            grid = derive(contract.model_copy(update={"modality": modality}), axes_for(modality))
            assert expected in {one.name for one in grid.cells}
