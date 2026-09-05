"""A mailbox answering an outbound call, which tests the agent and not the caller."""

from test_harness import _built_environment


def _mailbox(name: str, greeting: str, **overrides):
    """A voicemail scenario as a writer would submit one, against the cart world's catalogue."""
    from fi.alk.harness.scenario import Persona, Scenario

    payload = {
        "name": name,
        "instruction": "Play the greeting once and say nothing else for the rest of the call.",
        "persona": Persona(
            name=name,
            personality="patient",
            communication_style="brief",
            initial_message=greeting,
            languages=["English"],
            accent="American",
            keywords=["mailbox"],
        ),
        "fixture": {"origin": "seed"},
        "solution": [
            {"tool": "lst", "arguments": {}},
            {"tool": "add", "arguments": {"item_id": "big_mac"}},
        ],
        "sub_goals": ["item-added"],
        "call_direction": "outbound",
        "answered_by": "voicemail",
    }
    payload.update(overrides)
    return Scenario(**payload)


def _person(index: int):
    return _mailbox(
        f"person-{index}",
        f"Hello, this is caller {index}.",
        answered_by="",
        call_direction="",
    )


def test_a_mailbox_can_only_answer_a_call_the_agent_placed():
    """`call_direction` empty defers to the contract, so a voicemail scenario that does not state
    outbound itself is one the run may legally make inbound."""
    from fi.alk.harness.scenario import answered_by_problems

    assert answered_by_problems(_mailbox("named", "Hi, this is Dana.")) == []
    assert answered_by_problems(_person(1)) == []

    silent = answered_by_problems(_mailbox("undeclared", "Hi.", call_direction=""))
    assert silent and "must state call_direction 'outbound'" in silent[0]

    inbound = answered_by_problems(_mailbox("dialled-in", "Hi.", call_direction="inbound"))
    assert inbound and "only happens on a call the agent placed" in inbound[0]

    unknown = answered_by_problems(_mailbox("odd", "Hi.", answered_by="answering machine"))
    assert unknown and "answered_by must be" in unknown[0]


def test_validation_reports_a_mailbox_that_could_not_have_answered(tmp_path):
    """The rule reaches a writer through the same list every other problem does."""
    from fi.alk.harness.scenario import validate_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    said = " ".join(
        validate_scenario(
            _mailbox("dialled-in", "Hi.", call_direction="inbound"), catalogue, {}
        )
    )
    assert "only happens on a call the agent placed" in said


def test_voicemail_stays_a_minority_of_a_suite():
    """One narrow test, worth a few scenarios and never a theme."""
    from fi.alk.harness.scenario import suite_diversity_problems

    over = [_mailbox(f"box-{index}", f"Mailbox {index}.") for index in range(3)] + [
        _person(index) for index in range(3)
    ]
    assert "under a sixth of the suite" in " ".join(suite_diversity_problems(over))

    within = [_mailbox("box-only", "You have reached Dana.")] + [
        _person(index) for index in range(11)
    ]
    assert "under a sixth of the suite" not in " ".join(suite_diversity_problems(within))


def test_several_mailboxes_have_to_be_different_mailboxes():
    from fi.alk.harness.scenario import suite_diversity_problems

    same = [_mailbox(f"same-{index}", "Leave a message.") for index in range(3)] + [
        _person(index) for index in range(17)
    ]
    assert "use the same greeting" in " ".join(suite_diversity_problems(same))

    varied = [
        _mailbox("named", "You have reached Dana Whitfield."),
        _mailbox("carrier", "The person you called is not available."),
        _mailbox("full", "This mailbox is full."),
    ] + [_person(index) for index in range(17)]
    assert "use the same greeting" not in " ".join(suite_diversity_problems(varied))


def test_the_mailbox_replaces_the_callers_rules_rather_than_adding_to_them():
    from fi.alk.harness.simulator_voice import (
        SIMULATOR_INSTRUCTIONS,
        simulator_instructions,
    )

    mailbox = simulator_instructions("outbound", "unaware", "voicemail")
    assert "VOICEMAIL SYSTEM" in mailbox
    assert "Never end the call" in mailbox
    assert SIMULATOR_INSTRUCTIONS not in mailbox

    assert simulator_instructions() == SIMULATOR_INSTRUCTIONS
    assert simulator_instructions("inbound", "expecting") == SIMULATOR_INSTRUCTIONS
    assert "THIS CALL WAS PLACED TO YOU" in simulator_instructions("outbound", "unaware")


def test_the_simulator_definition_reads_the_mailbox_from_its_lane():
    from fi.alk.harness.simulator_voice import simulator_definition

    settings = {
        "SIMULATOR_LLM_PROVIDER": "google",
        "HARNESS_CALL_DIRECTION": "outbound",
        "HARNESS_ANSWERED_BY": "voicemail",
    }
    made = simulator_definition(lambda name: settings.get(name, ""))
    assert "VOICEMAIL SYSTEM" in made.instructions

    settings.pop("HARNESS_ANSWERED_BY")
    person = simulator_definition(lambda name: settings.get(name, ""))
    assert "VOICEMAIL SYSTEM" not in person.instructions


def test_a_judged_only_mailbox_is_refused_where_the_world_can_be_read(tmp_path):
    """The judged-only path in `prove` is for a world with nothing readable. A real world has rows,
    so a mailbox scenario there still has to name a sub-goal settled in code. Recorded rather than
    worked around."""
    from fi.alk.harness.prove import prove

    root, _contract, catalogue = _built_environment(tmp_path)

    judged = _mailbox("judged-mailbox", "You have reached Dana.", sub_goals=["polite"])
    proof = prove(judged, catalogue, root)
    assert not proof.holds and not proof.judged_only
    assert "is in code" in " ".join(proof.broken)

    settled = _mailbox("settled-mailbox", "You have reached Dana.")
    assert prove(settled, catalogue, root).holds
