"""Scenario and sub-goal files: prose is cleaned where each writer saves it, the closed list of
kept-exactly fields is never rewritten, and a credential in one of those is refused at submission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue, validate_sub_goal
from fi.alk.harness.scenario import Persona, Scenario, Step, validate_scenario

KEY = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5"
CHECK = "def check(world, calls):\n    return None\n"


def _scenario(**fields) -> Scenario:
    body = {
        "name": "reset",
        "instruction": f"Ask to reset it; the caller read out {KEY}.",
        "tests": "the reset flow",
        "keywords": [f"token {KEY}", "reset"],
        "persona": Persona(
            name="Ana",
            personality="calm and brief",
            metadata={"verification_token": "RT4821X"},
            scripted_caller={"reset_token": "RT4821X", "new_password": "Blue-Sky-42"},
        ),
        "variables": {"reset_token": "RT4821X"},
        "fixture": {"origin": "seed", "credentials": {"new_password": "Blue-Sky-42"}},
        "solution": [
            Step(
                tool="reset",
                arguments={"reset_token": "RT4821X", "new_password": "Blue-Sky-42"},
                environment_arguments={"reset_token": "RT4821X"},
                fixed={"new_password": "the caller"},
            )
        ],
        "alternatives": [[Step(tool="reset", arguments={"reset_token": "RT4821X"})]],
        "sub_goals": ["reset-done"],
    }
    body.update(fields)
    return Scenario(**body)


def _catalogue() -> Catalogue:
    return Catalogue(
        sub_goals=[
            SubGoal(
                name="reset-done",
                what=f"the reset happened; a note quoted {KEY}",
                check=CHECK + "# reset_token RT4821X\n",
                reference=f"from the caller, who said {KEY}",
            )
        ]
    )


def _assert_cleaned_but_exact(document: dict) -> None:
    assert KEY not in json.dumps(document)
    assert document["persona"]["metadata"] == {"verification_token": "RT4821X"}
    assert document["persona"]["scripted_caller"] == {
        "reset_token": "RT4821X",
        "new_password": "Blue-Sky-42",
    }
    assert document["variables"] == {"reset_token": "RT4821X"}
    assert document["fixture"]["credentials"] == {"new_password": "Blue-Sky-42"}
    step = document["solution"][0]
    assert step["arguments"] == {"reset_token": "RT4821X", "new_password": "Blue-Sky-42"}
    assert step["environment_arguments"] == {"reset_token": "RT4821X"}
    assert step["fixed"] == {"new_password": "the caller"}
    assert document["alternatives"][0][0]["arguments"] == {"reset_token": "RT4821X"}


def test_the_shared_helper_cleans_prose_and_keeps_the_closed_list():
    from fi.alk.harness.folder import clean_scenario_document

    document = _scenario().model_dump()
    document["persona"]["personality"] = f"calm, mentions {KEY}"
    cleaned = clean_scenario_document(document)
    _assert_cleaned_but_exact(cleaned)
    assert "[redacted]" in cleaned["instruction"]
    assert "[redacted]" in cleaned["keywords"][0]
    assert "[redacted]" in cleaned["persona"]["personality"]
    assert KEY in json.dumps(document), "the input is left as it was"
    assert clean_scenario_document(cleaned) == cleaned


def test_the_catalogue_helper_cleans_every_field_but_check():
    from fi.alk.harness.catalogue import clean_catalogue

    cleaned = clean_catalogue(_catalogue().model_dump())
    goal = cleaned["sub_goals"][0]
    assert KEY not in json.dumps(cleaned)
    assert "[redacted]" in goal["what"] and "[redacted]" in goal["reference"]
    assert goal["check"] == CHECK + "# reset_token RT4821X\n"


def test_write_folder_and_the_index_clean_the_scenario_file(tmp_path: Path):
    from fi.alk.harness.folder import read_folder, write_folder, write_index

    scenario = _scenario()
    root = write_folder(scenario, _catalogue(), tmp_path)
    saved = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
    _assert_cleaned_but_exact(saved)
    index = write_index([scenario], tmp_path)
    _assert_cleaned_but_exact(json.loads(index.read_text(encoding="utf-8"))[0])
    back = read_folder(tmp_path, "reset")
    assert back.solution[0].arguments["new_password"] == "Blue-Sky-42"
    assert back.persona.scripted_caller["reset_token"] == "RT4821X"


def test_save_catalogue_cleans_what_and_keeps_check(tmp_path: Path):
    path = save_catalogue(_catalogue(), tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert KEY not in json.dumps(saved)
    assert saved["sub_goals"][0]["check"] == CHECK + "# reset_token RT4821X\n"


def test_certify_only_pruning_cleans_the_scenarios_it_rewrites(tmp_path: Path):
    from fi.alk.harness.authoring_entrypoint import _prune_suite_to_catalogue

    output = tmp_path / "out"
    folder = output / "scenarios" / "reset"
    (folder / "checks").mkdir(parents=True)
    document = _scenario().model_dump()
    (folder / "scenario.json").write_text(json.dumps(document), encoding="utf-8")
    (folder / "setup.py").write_text("def setup(world):\n    return None\n")
    (folder / "ready.py").write_text("def ready(world):\n    return None\n")
    (output / "sub_goals.json").write_text(
        Catalogue(sub_goals=[SubGoal(name="reset-done", what="x", check=CHECK)]).model_dump_json(),
        encoding="utf-8",
    )
    _prune_suite_to_catalogue(output)
    _assert_cleaned_but_exact(json.loads((folder / "scenario.json").read_text(encoding="utf-8")))


def test_sealing_copies_scenarios_cleaned(tmp_path: Path):
    from fi.alk.harness.bundle_author_v2 import _copy_scenarios

    authoring, staging = tmp_path / "authoring", tmp_path / "staging"
    folder = authoring / "scenarios" / "reset"
    folder.mkdir(parents=True)
    staging.mkdir()
    (folder / "scenario.json").write_text(json.dumps(_scenario().model_dump()), encoding="utf-8")
    _copy_scenarios(authoring, staging, count=1)
    _assert_cleaned_but_exact(
        json.loads((staging / "scenarios" / "reset" / "scenario.json").read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("fixture", {"origin": "seed", "url": "https://x.example/api?api_key=abc"}),
        ("variables", {"link": f"/c?sig=abcDEF123%2F&k={KEY}"}),
        ("persona", Persona(name="Ana", metadata={"note": KEY})),
        ("persona", Persona(name="Ana", scripted_caller={"key": KEY})),
        ("solution", [Step(tool="t", arguments={"auth": f"Bearer {KEY}"})]),
        ("solution", [Step(tool="t", environment_arguments={"dsn": "postgres://u:hunter2@db/x"})]),
        ("alternatives", [[Step(tool="t", arguments={"x": KEY})]]),
        ("solution", [Step(tool="t", fixed={"x": KEY})]),
    ],
)
def test_a_credential_in_a_kept_field_is_refused_and_named(field, value):
    scenario = _scenario(**{field: value})
    problems = [
        one
        for one in validate_scenario(scenario, _catalogue(), {})
        if "holds a credential" in one
    ]
    assert len(problems) == 1
    assert problems[0].startswith(field if field != "persona" else "persona.")
    assert KEY not in problems[0]


def test_honest_scenario_data_is_not_refused():
    problems = validate_scenario(_scenario(), _catalogue(), {})
    assert not [one for one in problems if "holds a credential" in one]


def test_a_credential_in_check_code_is_refused_and_a_placeholder_is_not():
    bad = SubGoal(name="g", what="w", check=CHECK + 'password = "hunter2"\n')
    assert any("holds a credential" in one for one in validate_sub_goal(bad))
    good = SubGoal(name="g", what="w", check=CHECK + "password = '$PASSWORD'\n")
    assert not any("holds a credential" in one for one in validate_sub_goal(good))
    keyed = SubGoal(name="g", what="w", check=CHECK + f"# {KEY}\n")
    assert any("holds a credential" in one for one in validate_sub_goal(keyed))


# What the saved file would say differently from the document submitted: the caller is given the
# saved copy, and the folder is named from the name, so each must survive cleaning unchanged.
def _cleaning_problems(scenario: Scenario) -> list[str]:
    return [
        one
        for one in validate_scenario(scenario, _catalogue(), {})
        if "is changed when the scenario is cleaned" in one
    ]


def test_an_instruction_the_cleaner_would_change_is_refused_at_submission():
    scenario = _scenario(
        instruction="Reset your password. New password: Blue-Sky-42",
        solution=[
            Step(
                tool="reset",
                arguments={"new_password": "Blue-Sky-42"},
                fixed={"new_password": "caller: says it"},
            )
        ],
    )
    problems = _cleaning_problems(scenario)
    assert len(problems) == 1
    assert problems[0].startswith("instruction ")
    assert "fixture" in problems[0]
    assert "Blue-Sky-42" not in problems[0]


@pytest.mark.parametrize(
    "fields,start",
    [
        ({"persona": Persona(name="Ana", initial_message="my password: Blue-Sky-42")},
         "persona.initial_message "),
        ({"name": "login token: abc123"}, "name "),
        ({"scenario_key": "login token: abc123"}, "scenario_key "),
        ({"sub_goals": [f"goal {KEY}"]}, "sub_goals "),
    ],
)
def test_the_other_fields_the_run_replays_or_names_by_are_refused_too(fields, start):
    problems = _cleaning_problems(_scenario(instruction="Ask to reset it.", **fields))
    assert len(problems) == 1
    assert problems[0].startswith(start)


def test_an_honest_scenario_is_not_refused_for_cleaning():
    scenario = _scenario(
        instruction="Reset your password. The new password is in the fixture.",
        persona=Persona(name="Ana", initial_message="Hi, I need a password reset."),
    )
    assert _cleaning_problems(scenario) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "my password: Blue-Sky-42"),
        ("occupation", "token: abc123"),
        ("location", "token: abc123"),
        ("personality", "calm, my password: Blue-Sky-42"),
        ("communication_style", "token: abc123"),
        ("age_group", "token: abc123"),
        ("gender", "token: abc123"),
    ],
)
def test_the_persona_prose_the_caller_is_given_is_refused_when_cleaning_would_change_it(
    field, value
):
    persona = Persona(name="Ana", **{field: value}) if field != "name" else Persona(name=value)
    problems = _cleaning_problems(_scenario(instruction="Ask to reset it.", persona=persona))
    assert len(problems) == 1
    assert problems[0].startswith(f"persona.{field} ")
    assert "Blue-Sky-42" not in problems[0]


@pytest.mark.parametrize(
    "text",
    [
        "Look up the order at https://shop.example.com/orders?id=77 and read its status.",
        "Open https://shop.example.com/list?page=2 and count the rows.",
    ],
)
def test_an_instruction_with_an_honest_url_query_is_not_refused(text):
    assert _cleaning_problems(_scenario(instruction=text)) == []


def test_an_instruction_with_a_credential_in_a_query_is_still_refused():
    problems = _cleaning_problems(
        _scenario(instruction="Open https://shop.example.com/list?api_key=abc123def and read it.")
    )
    assert len(problems) == 1 and problems[0].startswith("instruction ")


# setup.py, ready.py and each check are copied into the scenario's folder as written.
def _copied_file_problems(scenario: Scenario, catalogue: Catalogue) -> list[str]:
    return [
        one
        for one in validate_scenario(scenario, catalogue, {})
        if "is copied into the scenario as written" in one
    ]


def _catalogue_with_check(check: str) -> Catalogue:
    return Catalogue(
        sub_goals=[SubGoal(name="reset-done", what="the reset happened", check=check)]
    )


@pytest.mark.parametrize(
    "file,fields,check",
    [
        ("setup.py", {"setup_code": 'def setup(world):\n    token = "ghp_' + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" + '"\n'}, CHECK),
        ("ready.py", {"ready_code": 'def ready(world):\n    password = "hunter2x"\n'}, CHECK),
        ("checks/reset-done.py", {}, CHECK + 'api_key = "abc123def"\n'),
    ],
)
def test_a_credential_in_a_copied_file_is_refused_and_the_file_is_named(file, fields, check):
    problems = _copied_file_problems(
        _scenario(instruction="Ask to reset it.", **fields), _catalogue_with_check(check)
    )
    assert len(problems) == 1
    assert problems[0].startswith(file + " ")
    assert "hunter2x" not in problems[0] and "abc123def" not in problems[0]


def test_honest_copied_files_are_not_refused():
    scenario = _scenario(
        instruction="Ask to reset it.",
        setup_code='def setup(world):\n    password = "$PASSWORD"\n    world.put("users", {"id": 1})\n',
        ready_code="def ready(world):\n    return True\n",
    )
    assert _copied_file_problems(scenario, _catalogue_with_check(CHECK + "# done\n")) == []


# The structured fields the world replays (an actor's scheduled arguments, a pre-run thread's
# arguments, a fault's approved_when) are kept as written and a credential in one is refused; the
# prose the agent or the actors are given (starts_with.text, an actor's instruction and knowledge)
# is refused when cleaning would change it.
AWS = "AKIA" + "IOSFODNN7EXAMPLE"


def _replayed(**fields) -> Scenario:
    return _scenario(instruction="Ask to reset it.", **fields)


STRUCTURED = [
    (
        "actors[0].actions[0].arguments",
        {"actors": [{"id": "it_desk", "actions": [{"action": "rotate", "arguments": {"key_id": AWS}}]}]},
    ),
    (
        "pre_run[0].thread[0].arguments",
        {
            "pre_run": [
                {"actors": ["it_desk"], "thread": [{"actor": "it_desk", "action": "post", "arguments": {"text": AWS}}]}
            ]
        },
    ),
    (
        "faults[0].approved_when",
        {
            "faults": [
                {"action": "rotate", "mode": "approval_required", "approval": "approve", "approved_when": {"key_id": AWS}}
            ]
        },
    ),
    # Matched against the world's records to decide whether the actor comes in, on the actor
    # and on each of its scheduled actions.
    (
        "actors[0].trigger.where",
        {
            "actors": [
                {"id": "it_desk", "trigger": {"kind": "conditional", "collection": "keys", "where": {"key_id": AWS}}}
            ]
        },
    ),
    (
        "actors[0].actions[0].trigger.where",
        {
            "actors": [
                {
                    "id": "it_desk",
                    "actions": [
                        {
                            "action": "rotate",
                            "trigger": {"kind": "conditional", "collection": "keys", "where": {"key_id": AWS}},
                        }
                    ],
                }
            ]
        },
    ),
]

PROSE = [
    ("starts_with.text ", {"starts_with": {"text": f"Please rotate key {AWS}"}}),
    ("actors[0].instruction ", {"actors": [{"id": "it_desk", "instruction": f"You hold {AWS}"}]}),
    ("actors[0].knowledge ", {"actors": [{"id": "it_desk", "knowledge": [{"kind": "facts", "text": f"the key is {AWS}"}]}]}),
    # The error the world returns to the agent, which the call runner matches on.
    (
        "faults[0].message ",
        {"faults": [{"action": "rotate", "mode": "error", "status": 403, "message": f"key {AWS} is revoked"}]},
    ),
]


@pytest.mark.parametrize("field,fields", STRUCTURED, ids=[one[0] for one in STRUCTURED])
def test_a_credential_in_a_structured_replayed_field_is_refused_named_and_kept(field, fields):
    from fi.alk.harness.folder import clean_scenario_document, document_for

    scenario = _replayed(**fields)
    problems = [one for one in validate_scenario(scenario, _catalogue(), {}) if "holds a credential" in one]
    assert len(problems) == 1 and problems[0].startswith(field)
    assert AWS not in problems[0]
    # Never rewritten, so a refused value cannot be saved as a masked one.
    assert AWS in json.dumps(clean_scenario_document(document_for(scenario)))


@pytest.mark.parametrize(
    "field,trigger_on",
    [("actors[0].trigger.where", "actor"), ("actors[0].actions[0].trigger.where", "action")],
)
def test_a_shapeless_value_under_a_credential_name_in_where_is_refused(field, trigger_on):
    trigger = {"kind": "conditional", "collection": "accounts", "where": {"password": "hunter2"}}
    actor = {"id": "it_desk"}
    if trigger_on == "actor":
        actor["trigger"] = trigger
    else:
        actor["actions"] = [{"action": "rotate", "trigger": trigger}]

    problems = [
        one
        for one in validate_scenario(_replayed(actors=[actor]), _catalogue(), {})
        if "holds a credential" in one
    ]

    assert len(problems) == 1 and problems[0].startswith(field)
    assert "hunter2" not in problems[0]


def test_an_honest_where_with_a_credential_word_first_or_a_yes_no_is_kept():
    trigger = {
        "kind": "conditional",
        "collection": "accounts",
        "where": {"password_reset_status": "pending", "has_credentials": True},
    }
    scenario = _replayed(actors=[{"id": "it_desk", "trigger": trigger}])
    assert not [
        one for one in validate_scenario(scenario, _catalogue(), {}) if "holds a credential" in one
    ]


@pytest.mark.parametrize("start,fields", PROSE, ids=[one[0].strip() for one in PROSE])
def test_replayed_prose_the_cleaner_would_change_is_refused(start, fields):
    problems = _cleaning_problems(_replayed(**fields))
    assert len(problems) == 1 and problems[0].startswith(start)
    assert AWS not in problems[0]


def test_honest_structured_and_prose_replayed_fields_are_not_refused():
    scenario = _replayed(
        starts_with={"text": "Please rotate the key for the it desk"},
        actors=[
            {
                "id": "it_desk",
                "instruction": "You hold the shared key",
                "knowledge": [{"kind": "facts", "text": "keys rotate monthly"}],
                "actions": [{"action": "rotate", "arguments": {"key_id": "$KEY_ID"}}],
            }
        ],
        faults=[
            {"action": "rotate", "mode": "approval_required", "approval": "approve", "approved_when": {"key_id": "k-77"}},
            {"action": "rotate", "mode": "error", "status": 403, "message": "key k-77 is revoked"},
        ],
    )
    assert _cleaning_problems(scenario) == []
    assert not [one for one in validate_scenario(scenario, _catalogue(), {}) if "holds a credential" in one]
