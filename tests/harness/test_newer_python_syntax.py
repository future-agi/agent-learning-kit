"""The kit reads customers' Python on the Python it runs on, the hosted image's 3.12. A file
written for 3.13's or 3.14's syntax doesn't parse there; the reading says so, naming the file
and the Python it needs, instead of reading the agent in part without a word."""

from __future__ import annotations

import sys

import pytest

from fi.alk.harness.python_source import newer_syntax, parsed_or_syntax_error, unparsed_note

pytestmark = pytest.mark.skipif(
    sys.version_info >= (3, 13), reason="the kit's own interpreter is 3.12; 3.13 parses these"
)


@pytest.mark.parametrize(
    ("source", "python", "construct"),
    [
        ("class Box[T=int]:\n    item: T\n", "3.13", "a type parameter default"),
        ("def first[T=str](items: list[T]) -> T:\n    return items[0]\n", "3.13",
         "a type parameter default"),
        ("type Pair[T=int] = tuple[T, T]\n", "3.13", "a type parameter default"),
        ("def greet(name):\n    return t'hi {name}'\n", "3.14", "a template string"),
        ("x = T'{a}'\ny = tr'raw {b}'\n", "3.14", "a template string"),
        ("try:\n    pass\nexcept ValueError, TypeError:\n    pass\n", "3.14",
         "an except clause without parentheses"),
    ],
)
def test_a_newer_python_construct_is_named(source, python, construct) -> None:
    assert newer_syntax(source) == (python, construct)


@pytest.mark.parametrize(
    "source",
    [
        "def f(:\n    pass\n",  # a plain syntax error
        "x = t + 'a'\n",  # a name t, then a string: valid
        "class Box[T: int]:\n    pass\n",  # 3.12 syntax, parses
        "try:\n    pass\nexcept (ValueError, TypeError):\n    pass\n",
        "d = {'a': 1}\nprint(d['a'])\n",
        # A newer construct that is not the whole failure: 3.14 can't parse these either.
        'try:\n    pass\nexcept ValueError, e:\n    print "hello"\n',
        'x = t"a"\nprint "b"\n',
    ],
)
def test_nothing_else_is_called_newer_syntax(source) -> None:
    assert newer_syntax(source) is None
    tree = parsed_or_syntax_error(source)
    if isinstance(tree, SyntaxError):
        assert newer_syntax(source, line=tree.lineno) is None


def test_a_file_a_newer_construct_does_not_explain_is_a_plain_error(tmp_path) -> None:
    (tmp_path / "py2").mkdir()
    (tmp_path / "py2" / "legacy.py").write_text(
        'try:\n    pass\nexcept ValueError, e:\n    print "hello"\n', encoding="utf-8"
    )
    (tmp_path / "mix").mkdir()
    (tmp_path / "mix" / "both.py").write_text('x = t"a"\nprint "b"\n', encoding="utf-8")

    assert {one["path"]: one["python"] for one in unparsed_note(tmp_path)["unparsed"]} == {
        "mix/both.py": "",
        "py2/legacy.py": "",
    }


def test_the_newest_construct_on_the_stopping_line_is_named() -> None:
    text = 'class Box[T=int](t"x"):\n    pass\n'
    assert newer_syntax(text) == ("3.14", "a template string")
    assert newer_syntax(text, line=parsed_or_syntax_error(text).lineno) == (
        "3.14",
        "a template string",
    )


def test_the_files_the_kits_python_cannot_parse_are_listed(tmp_path) -> None:
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "agent" / "box.py").write_text("class Box[T=int]:\n    item: T\n", encoding="utf-8")
    (tmp_path / "agent" / "greet.py").write_text("g = t'hi {n}'\n", encoding="utf-8")
    (tmp_path / "agent" / "broken.py").write_text("def f(:\n", encoding="utf-8")
    # Not the agent's code: left out, as the tool evidence leaves them out.
    for skipped in ("tests", "venv", ".git"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "new.py").write_text("g = t'x'\n", encoding="utf-8")

    note = unparsed_note(tmp_path)

    assert set(note) == {"unparsed"}
    by_path = {one["path"]: one for one in note["unparsed"]}
    assert set(by_path) == {"agent/box.py", "agent/greet.py", "agent/broken.py"}
    assert by_path["agent/box.py"]["python"] == "3.13"
    assert by_path["agent/greet.py"]["python"] == "3.14"
    # Empty, never None: the platform reads "3.13", "3.14" or nothing.
    assert by_path["agent/broken.py"]["python"] == ""
    assert by_path["agent/box.py"]["reason"] == (
        "agent/box.py uses a type parameter default, which needs Python 3.13; the kit reads "
        "source with Python 3.12, so this file was not read"
    )
    # The parser's message can repeat the customer's code, so only the line is kept.
    assert by_path["agent/broken.py"]["reason"] == (
        "agent/broken.py doesn't parse on Python 3.12 (line 1), so this file was not read"
    )


def test_a_subfolder_limits_the_files_listed(tmp_path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "a.py").write_text("g = t'x'\n", encoding="utf-8")
    (tmp_path / "two" / "b.py").write_text("g = t'x'\n", encoding="utf-8")

    assert [one["path"] for one in unparsed_note(tmp_path, "one")["unparsed"]] == ["one/a.py"]


def test_an_agent_whose_files_all_parse_has_no_note(tmp_path) -> None:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")

    assert unparsed_note(tmp_path) == {}


def test_the_list_stops_at_fifty_and_says_it_was_cut(tmp_path) -> None:
    for number in range(51):
        (tmp_path / f"m{number:02d}.py").write_text("g = t'x'\n", encoding="utf-8")

    note = unparsed_note(tmp_path)

    assert len(note["unparsed"]) == 50
    assert note["unparsed_truncated"] is True


def test_fifty_files_fit_without_the_flag(tmp_path) -> None:
    for number in range(50):
        (tmp_path / f"m{number:02d}.py").write_text("g = t'x'\n", encoding="utf-8")

    note = unparsed_note(tmp_path)

    assert len(note["unparsed"]) == 50
    assert "unparsed_truncated" not in note


def test_a_path_the_platform_cannot_carry_is_counted_but_not_named(tmp_path) -> None:
    # A name outside the kit's path rule is the customer's own text: it is never copied into
    # the document, and the flag says the list is not the whole story.
    (tmp_path / "my agent.py").write_text("g = t'x'\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("g = t'x'\n", encoding="utf-8")

    note = unparsed_note(tmp_path)

    assert [one["path"] for one in note["unparsed"]] == ["ok.py"]
    assert note["unparsed_truncated"] is True


def test_only_a_path_the_platform_cannot_carry_still_leaves_the_flag(tmp_path) -> None:
    (tmp_path / "my agent.py").write_text("g = t'x'\n", encoding="utf-8")

    assert unparsed_note(tmp_path) == {"unparsed_truncated": True}


def test_files_past_the_parse_budget_leave_the_flag(tmp_path, monkeypatch) -> None:
    from fi.alk.harness import python_source

    monkeypatch.setattr(python_source, "_UNPARSED_WALK_TOTAL", 12)
    (tmp_path / "a.py").write_text("g = t'x'\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("g = t'x'\n", encoding="utf-8")

    note = unparsed_note(tmp_path)

    assert [one["path"] for one in note["unparsed"]] == ["a.py"]
    assert note["unparsed_truncated"] is True


# --- every place that parses source takes its cautious branch -------------------------------
# A file it can't parse must not read as a file with nothing in it.

_T_STRING = "label = t'{x}'\n"


def test_a_check_that_does_not_parse_is_named_not_passed_over(tmp_path) -> None:
    from fi.alk.harness.folder import check_problems

    checks = tmp_path / "scenarios" / "refund" / "checks"
    checks.mkdir(parents=True)
    (checks / "refund_amount.py").write_text(
        "def check(world, calls):\n"
        "    refunds = [c for c in calls if c.name == 'refund']\n"
        "    note = t'{refunds}'\n"
        "    return None if refunds and refunds[0].arguments['amount'] else note\n",
        encoding="utf-8",
    )

    assert check_problems(tmp_path) == [
        "refund: refund_amount doesn't parse on Python 3.12, so it was not compared with the "
        "scenario's other checks"
    ]


def test_a_tool_defined_in_a_file_that_does_not_parse_is_still_found(tmp_path) -> None:
    from fi.alk.harness.source_tool_evidence import defined_tool_names

    (tmp_path / "tools.py").write_text(
        _T_STRING
        + "async def lookup_order(order_id):\n    return order_id\n"
        + "def refund[T](amount: T) -> T:\n    return amount\n"
        + "search = Tool(name=\"search_catalogue\", fn=print)\n",
        encoding="utf-8",
    )

    assert {"lookup_order", "refund", "search_catalogue"} <= defined_tool_names(tmp_path)


def test_a_callback_in_a_file_that_does_not_parse_is_still_found(tmp_path) -> None:
    from fi.alk.harness.bundle_author_v2 import _discover_callback_entrypoint

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "agent.py").write_text(
        _T_STRING + "async def agent_callback(message):\n    return message\n", encoding="utf-8"
    )
    (tmp_path / "app" / "helpers.py").write_text(
        _T_STRING + "class Box:\n    def agent_callback(self):\n        pass\n", encoding="utf-8"
    )

    assert _discover_callback_entrypoint(tmp_path) == "app.agent:agent_callback"


@pytest.mark.parametrize(
    ("imports", "expected"),
    [
        ("from fi_instrumentation import register\n", "traceai"),
        (
            "from opentelemetry.sdk.trace import TracerProvider\n"
            "from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter\n",
            "opentelemetry",
        ),
    ],
)
def test_tracing_imported_in_a_file_that_does_not_parse_is_still_seen(
    tmp_path, imports, expected
) -> None:
    from fi.alk.harness.instrumentation_evidence import detect_instrumentation

    (tmp_path / "agent.py").write_text(imports + _T_STRING, encoding="utf-8")

    assert detect_instrumentation(tmp_path) == expected


def test_a_plugin_key_used_in_a_file_that_does_not_parse_is_still_asked_for() -> None:
    from fi.alk.harness.credentials import _python_sdk_requirements

    content = (
        "from livekit.plugins import deepgram, elevenlabs, google\n"
        + _T_STRING
        + "stt = deepgram.STT(model='nova-3')\n"
        + "tts = elevenlabs.TTS(api_key=key_from(t'{x}'))\n"
        + "llm = google.LLM(model='gemini-2.5-flash', vertexai=True)\n"
    )

    assert _python_sdk_requirements(content) == [
        ("DEEPGRAM_API_KEY", "sdk:livekit.plugins.deepgram"),
        # Whether api_key= is passed can't be read: both names, as for **opts.
        ("ELEVENLABS_API_KEY", "sdk:livekit.plugins.elevenlabs"),
        ("ELEVEN_API_KEY", "sdk:livekit.plugins.elevenlabs"),
        ("GOOGLE_APPLICATION_CREDENTIALS", "sdk:livekit.plugins.google.vertex"),
        ("GOOGLE_APPLICATION_CREDENTIALS_JSON", "sdk:livekit.plugins.google.vertex"),
        ("GOOGLE_CLOUD_PROJECT", "sdk:livekit.plugins.google.vertex"),
    ]


@pytest.mark.parametrize(
    ("client", "origin"),
    [
        (
            "from langchain_google_genai import ChatGoogleGenerativeAI\n"
            "model = ChatGoogleGenerativeAI(model='gemini', vertexai=True)\n",
            "sdk:langchain_google_genai.ChatGoogleGenerativeAI.vertex",
        ),
        (
            "from google import genai\nclient = genai.Client(vertexai=True)\n",
            "sdk:google.genai.Client.vertex",
        ),
    ],
)
def test_a_vertex_client_in_a_file_that_does_not_parse_still_asks_for_google_credentials(
    client, origin
) -> None:
    from fi.alk.harness.credentials import _python_sdk_requirements

    assert _python_sdk_requirements(client + _T_STRING) == [
        ("GOOGLE_APPLICATION_CREDENTIALS", origin),
        ("GOOGLE_APPLICATION_CREDENTIALS_JSON", origin),
        ("GOOGLE_CLOUD_PROJECT", origin),
    ]


# --- the walk reaches what the readers read, and says when it stops ---------------------------


def test_a_tool_module_outside_the_agents_folder_is_listed(tmp_path) -> None:
    # The tool check resolves an entry's module from the folder, folder/src, the root and
    # root/src: a module it can't parse at root/src is named too.
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tools.py").write_text(_T_STRING, encoding="utf-8")

    note = unparsed_note(tmp_path, "one", tool_modules=["tools"])

    assert [one["path"] for one in note["unparsed"]] == ["src/tools.py"]


def test_code_the_agent_imports_from_outside_its_folder_is_listed(tmp_path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "agent.py").write_text("import shared.helpers\n", encoding="utf-8")
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "shared" / "helpers.py").write_text(_T_STRING, encoding="utf-8")

    note = unparsed_note(tmp_path, "one")

    assert [one["path"] for one in note["unparsed"]] == ["shared/helpers.py"]


def test_an_agent_of_many_ordinary_modules_is_walked_to_the_end(tmp_path) -> None:
    # 80 modules of 4 KB pass the scans' 256 KB total; the walk has its own.
    for number in range(80):
        (tmp_path / f"m{number:02d}.py").write_text("x = 1\n" * 700, encoding="utf-8")
    (tmp_path / "tools.py").write_text(_T_STRING, encoding="utf-8")

    assert unparsed_note(tmp_path) == {
        "unparsed": [
            {
                "path": "tools.py",
                "python": "3.14",
                "reason": "tools.py uses a template string, which needs Python 3.14; the kit "
                "reads source with Python 3.12, so this file was not read",
            }
        ]
    }


def test_a_file_too_large_to_check_leaves_the_flag(tmp_path) -> None:
    (tmp_path / "agent.py").write_text(_T_STRING + "x = 1\n" * 23_000, encoding="utf-8")

    assert unparsed_note(tmp_path) == {"unparsed_truncated": True}


def test_a_file_that_runs_the_parser_out_of_stack_leaves_the_flag(tmp_path) -> None:
    (tmp_path / "agent.py").write_text("x = " + "-" * 7000 + "1\n", encoding="utf-8")

    assert unparsed_note(tmp_path) == {"unparsed_truncated": True}


# --- naming the construct: bounded, and only where the parser stopped (M1, M2, L1) -----------


def test_naming_the_construct_stops_when_the_token_budget_runs_out() -> None:
    from fi.alk.harness.python_source import TokenBudget

    assert newer_syntax(_T_STRING, budget=TokenBudget(work=0)) is None


def test_a_long_one_line_file_is_named_quickly(tmp_path) -> None:
    import time

    # Unbounded, this took 15.7 s to tokenize.
    (tmp_path / "agent.py").write_text("a," * 64_000 + " :\n", encoding="utf-8")

    started = time.monotonic()
    note = unparsed_note(tmp_path)

    assert time.monotonic() - started < 5
    assert note["unparsed"][0]["python"] == ""


def test_a_python_2_file_is_a_plain_error_not_newer_syntax(tmp_path) -> None:
    # Its except clause is 3.14's shape, but the parser stops at line 1, where 3.14 stops too.
    text = "print 'hi'\ntry:\n    pass\nexcept ValueError, e:\n    pass\n"
    (tmp_path / "old.py").write_text(text, encoding="utf-8")

    assert newer_syntax(text, line=1) is None
    assert unparsed_note(tmp_path)["unparsed"] == [
        {
            "path": "old.py",
            "python": "",
            "reason": "old.py doesn't parse on Python 3.12 (line 1), so this file was not read",
        }
    ]


def test_an_except_list_starting_with_a_parenthesised_name_is_named() -> None:
    assert newer_syntax("try:\n    pass\nexcept (KeyError), ValueError:\n    pass\n") == (
        "3.14",
        "an except clause without parentheses",
    )


# --- a key only when it has something to say, and the walk's limits ---------------------------


def test_an_agent_whose_files_parse_but_one_is_too_large_says_only_that(tmp_path) -> None:
    # Nothing unparsed is known, so no list; one file wasn't checked, so the flag.
    (tmp_path / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "generated_pb2.py").write_text("x = 1\n" * 23_000, encoding="utf-8")

    assert unparsed_note(tmp_path) == {"unparsed_truncated": True}


def test_a_tool_module_in_a_tests_package_is_listed_without_a_subfolder(tmp_path) -> None:
    # The walk leaves tests/ out, but the tool check reads the module an entry names.
    (tmp_path / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "tools.py").write_text(_T_STRING, encoding="utf-8")

    note = unparsed_note(tmp_path, tool_modules=["tests.tools"])

    assert [one["path"] for one in note["unparsed"]] == ["tests/tools.py"]


def test_a_file_that_cannot_be_decoded_leaves_the_flag(tmp_path) -> None:
    (tmp_path / "agent.py").write_bytes(b"\xff\xfe" + _T_STRING.encode())
    (tmp_path / "escaped.py").write_text("# coding: unicode-escape\n" + _T_STRING, encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")

    assert unparsed_note(tmp_path) == {"unparsed_truncated": True}


def test_an_except_clause_scan_stays_linear(tmp_path) -> None:
    import time

    # Unbounded, this took 7.2 s, each except scanning forward over all the others.
    text = "x = (\n" + "except\n" * 9_000 + ")\n"
    started = time.monotonic()

    assert newer_syntax(text) is None
    assert time.monotonic() - started < 1


def test_past_the_file_count_the_walk_stops_and_says_so(tmp_path, monkeypatch) -> None:
    from fi.alk.harness import python_source

    monkeypatch.setattr(python_source, "_UNPARSED_WALK_FILES", 3)
    for number in range(4):
        (tmp_path / f"m{number}.py").write_text("x = 1\n", encoding="utf-8")

    assert unparsed_note(tmp_path) == {"unparsed_truncated": True}


def test_a_link_out_of_the_source_is_not_read(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.py").write_text(_T_STRING, encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "linked.py").symlink_to(outside / "elsewhere.py")

    assert unparsed_note(source) == {}


def test_a_tool_module_whose_path_cannot_be_checked_is_skipped(tmp_path) -> None:
    # A first part longer than the system allows in a file name: checking it raises OSError.
    (tmp_path / "agent.py").write_text("x = 1\n", encoding="utf-8")

    assert unparsed_note(tmp_path, None, ["a" * 300 + ".tools"]) == {}
