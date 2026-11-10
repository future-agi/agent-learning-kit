from __future__ import annotations

import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fi.alk.harness import scan_document as sd
from fi.alk.harness.scan_document import (
    Row,
    ScanResultInvalid,
    choose_rows,
    finish,
    root_name,
    wire_problems,
    write_agents_json,
)

FIXTURE = Path(__file__).parent / "fixtures" / "agent_scan_e07d56fcd.json"
SHA = "0123456789abcdef0123456789abcdef01234567"
PROBLEM_NAME = re.compile(r"[a-z0-9_.\[\]]+")


@dataclass(frozen=True)
class _Candidate:
    """The walk's candidate, with the fields the document reads."""

    entry: str
    subfolder: str = ""
    sign: str = "framework"
    side: bool = False
    builds: tuple[str, ...] = ("langgraph",)
    build_may_stop: str = ""
    partial: bool = False
    env_names: tuple[str, ...] = ("OPENAI_API_KEY",)
    tools: int = 2
    prompt_files: int = 1
    evidence: tuple[tuple[str, str, int], ...] = ()


def _framework(name: str = "langgraph", label: str | None = None) -> dict[str, Any]:
    return {"name": name, "version": None, "label": label}


def _row(
    entry: str,
    subfolder: str = "",
    *,
    framework: dict[str, Any] | None = None,
    finished: bool = True,
    found_by_ai: bool = False,
    **candidate: Any,
) -> Row:
    return Row(
        candidate=_Candidate(entry=entry, subfolder=subfolder, **candidate),
        framework=framework if framework is not None else _framework(),
        finished=finished,
        found_by_ai=found_by_ai,
    )


def _finish(
    rows: list[Row],
    *,
    root: str = "shop",
    commit_sha: str | None = SHA,
    truncated: bool = False,
    error: tuple[str, str] | None = None,
) -> tuple[dict[str, Any], bytes, bool]:
    body, dropped = finish(
        rows=rows, root_name=root, commit_sha=commit_sha, truncated=truncated, error=error
    )
    document = json.loads(body)
    assert wire_problems(document) == []
    return document, body, dropped


def _confidence(*rows: Row) -> list[str]:
    document, _, _ = _finish(list(rows))
    return [agent["confidence"] for agent in document["agents"]]


def test_found_lists_each_agent_in_the_platform_format() -> None:
    graph = _row(
        "services/support/graph.py",
        "services/support",
        env_names=("OPENAI_API_KEY", "SUPPORT_DB_URL"),
        tools=3,
        evidence=(("builds langgraph", "services/support/graph.py", 12),),
    )
    root = _row(
        "main.py",
        framework=_framework("other", "llama_index"),
        builds=("llama_index",),
        tools=0,
        prompt_files=0,
    )

    document, _, dropped = _finish([root, graph])

    assert dropped is False
    assert list(document) == [
        "schema_version",
        "status",
        "commit_sha",
        "agents",
        "truncated",
        "error",
    ]
    assert document == {
        "schema_version": "futureagi.agent-scan.v1",
        "status": "found",
        "commit_sha": SHA,
        "agents": [
            {
                "name": "support",
                "subfolder": "services/support",
                "entry": "services/support/graph.py",
                "framework": {"name": "langgraph", "version": None, "label": None},
                "confidence": "high",
                "evidence": ["Builds with LangGraph in services/support/graph.py, line 12."],
                "env_names": ["OPENAI_API_KEY", "SUPPORT_DB_URL"],
                "estimates": {"tools": 3, "prompt_files": 1},
            },
            {
                "name": "shop",
                "subfolder": "",
                "entry": "main.py",
                "framework": {"name": "other", "version": None, "label": "llama_index"},
                "confidence": "medium",
                "evidence": [],
                "env_names": ["OPENAI_API_KEY"],
                "estimates": {"tools": 0, "prompt_files": 0},
            },
        ],
        "truncated": False,
        "error": None,
    }
    assert [list(agent) for agent in document["agents"]] == [list(sd._AGENT_KEYS)] * 2
    assert sd._AGENT_KEYS == (
        "name",
        "subfolder",
        "entry",
        "framework",
        "confidence",
        "evidence",
        "env_names",
        "estimates",
    )

    none, _, _ = _finish([])
    assert (none["status"], none["agents"], none["error"]) == ("none", [], None)
    error, _, _ = _finish([], error=("scan_timed_out", "The scan ran out of time."))
    assert error["status"] == "error"
    assert error["error"] == {"code": "scan_timed_out", "message": "The scan ran out of time."}
    assert error["truncated"] is False


def test_confidence_rules() -> None:
    assert _confidence(_row("a.py", "a")) == ["high"]
    assert _confidence(_row("a.py", "a", builds=("langchain",))) == ["medium"]
    assert _confidence(_row("a.py", "a", framework=_framework("other", "dspy"))) == ["medium"]
    assert _confidence(_row("a.py", "a", framework=_framework("unknown"))) == ["low"]
    assert _confidence(_row("a.py", "a", framework=_framework("none"), builds=())) == ["low"]
    assert _confidence(_row("a.py", "a", found_by_ai=True)) == ["low"]
    assert _confidence(_row("a.py", "a", build_may_stop="graphs")) == ["low"]
    assert _confidence(_row("a.py", "a", build_may_stop="agent_py")) == ["low"]
    assert _confidence(_row("a.py", "a", partial=True)) == ["low"]
    assert _confidence(_row("a.py", "a", finished=False)) == ["low"]
    # Two agents in one folder both build cleanly, but a build of that folder can't pick one.
    assert _confidence(_row("a/one.py", "a"), _row("a/two.py", "a")) == ["low", "low"]
    assert _confidence(_row("a/one.py", "a"), _row("b/two.py", "b")) == ["high", "high"]

    document, _, _ = _finish(
        [
            _row("tests/t.py", "tests", side=True),
            _row("c/low.py", "c", framework=_framework("unknown")),
            _row("b/medium.py", "b", builds=()),
            _row("z/high.py", "z"),
            _row("a/high.py", "a"),
        ]
    )
    assert [agent["entry"] for agent in document["agents"]] == [
        "a/high.py",
        "z/high.py",
        "b/medium.py",
        "c/low.py",
        "tests/t.py",
    ]


def test_sharing_a_folder_is_decided_over_the_rows_written() -> None:
    shares = sd._TEMPLATES["shares_folder"]

    # The neighbour breaks the platform's path rule, so it isn't written and nothing is shared.
    document, _, dropped = _finish([_row("svc/main.py", "svc"), _row("svc/../x.py", "svc")])
    assert dropped is True
    assert [agent["entry"] for agent in document["agents"]] == ["svc/main.py"]
    assert document["agents"][0]["confidence"] == "high"
    assert shares not in document["agents"][0]["evidence"]

    # The neighbour is the fifty-first, cut by the cap.
    rows = [_row(f"f{i:02}/main.py", f"f{i:02}") for i in range(50)]
    rows.append(_row("f00/tests/test_main.py", "f00", side=True))
    document, _, dropped = _finish(rows)
    assert dropped is True
    assert len(document["agents"]) == 50
    assert {agent["confidence"] for agent in document["agents"]} == {"high"}

    # The neighbour repeats an entry already listed.
    document, _, _ = _finish(
        [_row("svc/main.py", "svc"), _row("svc/main.py", "svc", found_by_ai=True)]
    )
    assert [(agent["entry"], agent["confidence"]) for agent in document["agents"]] == [
        ("svc/main.py", "high")
    ]

    document, _, _ = _finish([_row("svc/main.py", "svc"), _row("svc/other.py", "svc")])
    assert all(shares in agent["evidence"] for agent in document["agents"])


def test_an_uppercase_commit_is_lowercased() -> None:
    assert _finish([_row("a.py")], commit_sha=SHA.upper())[0]["commit_sha"] == SHA
    assert _finish([_row("a.py")], commit_sha="0123456789ABCdef" * 2 + "01234567")[0][
        "commit_sha"
    ] == SHA
    assert _finish([_row("a.py")], commit_sha=None)[0]["commit_sha"] is None
    assert _finish([_row("a.py")], commit_sha=SHA[:39])[0]["commit_sha"] is None
    assert _finish([_row("a.py")], commit_sha="g" * 40)[0]["commit_sha"] is None
    assert _finish([_row("a.py")], commit_sha=SHA + "\n")[0]["commit_sha"] is None


def test_rows_and_an_error_together_keep_the_rows() -> None:
    error = ("scan_timed_out", "The scan ran out of time.")

    document, _, dropped = _finish([_row("a/main.py", "a")], error=error)
    assert document["status"] == "found"
    assert document["error"] is None
    assert document["truncated"] is True
    assert [agent["entry"] for agent in document["agents"]] == ["a/main.py"]
    assert dropped is False

    # Every row given breaks a rule: nothing is left to list, and the error was already dropped.
    document, _, dropped = _finish([_row("a/../main.py", "a")], error=error)
    assert (document["status"], document["agents"], document["error"]) == ("none", [], None)
    assert document["truncated"] is True
    assert dropped is True


def test_names_evidence_and_paths_always_fit_the_platform_rules() -> None:
    def job(agent_name: Any = None, repository: Any = None) -> Any:
        metadata = {} if agent_name is None else {"agent_name": agent_name}
        return SimpleNamespace(metadata=metadata, source=SimpleNamespace(repository=repository))

    assert root_name(job(" Support\u2003Bot\x00 ", "org/shop.git")) == "Support Bot"
    assert root_name(job(3, "org/shop.git")) == "shop"
    assert root_name(job("\u200b\u3000", "https://github.com/org/shop.git/")) == "shop"
    assert root_name(job(None, "shop")) == "shop"
    assert root_name(job(None, ".git")) == "agent"
    assert root_name(job()) == "agent"

    document, _, _ = _finish(
        [
            _row("a/svc/main.py", "a/svc"),
            _row("b/svc/app.py", "b/svc"),
            _row("c/api/main.py", "c/api"),
            _row("d/api/main.py", "d/api"),
            _row("main.py"),
            _row("server.py"),
        ],
        root="shop",
    )
    assert sorted(agent["name"] for agent in document["agents"]) == [
        "api (c/api/main.py)",
        "api (d/api/main.py)",
        "shop (main.py)",
        "shop (server.py)",
        "svc (app.py)",
        "svc (main.py)",
    ]
    assert _finish([_row("main.py")], root="x" * 300)[0]["agents"][0]["name"] == "x" * 255
    assert _finish([_row("main.py")], root="\u200b\x00")[0]["agents"][0]["name"] == "agent"

    long_path = "deep/" * 90 + "graph.py"
    facts = tuple(("builds langgraph", f"f{i}/graph.py", i + 1) for i in range(7))
    row = _row(
        long_path,
        "deep",
        evidence=(("builds langgraph", long_path, 4), *facts),
        env_names=tuple(f"NAME_{i}" for i in range(60)),
        build_may_stop="graphs",
        partial=True,
        finished=False,
    )
    agent = _finish([row])[0]["agents"][0]
    assert len(agent["evidence"]) == 5
    assert all(0 < len(sentence) <= 200 for sentence in agent["evidence"])
    assert agent["evidence"][0].startswith("Builds with LangGraph in …/")
    assert agent["evidence"][0].endswith("deep/graph.py, line 4.")
    assert agent["evidence"][1:4] == [
        sd._TEMPLATES["graphs_may_stop"],
        sd._TEMPLATES["not_finished"],
        sd._TEMPLATES["partial"],
    ]
    assert agent["env_names"] == [f"NAME_{i}" for i in range(50)]

    assert sd._LONGEST_TEMPLATE == max(len(template) for template in sd._TEMPLATES.values())
    assert sd._LONGEST_TEMPLATE + sd._PATH_ROOM <= sd.EVIDENCE_MAX
    assert sd._PATH_ROOM == 120

    aside = (("openai_agents_set_aside", "", 0),)
    assert sd._TEMPLATES["openai_agents_set_aside"] in _finish(
        [_row("a/main.py", "a", framework=_framework("unknown"), evidence=aside)]
    )[0]["agents"][0]["evidence"]
    assert _finish(
        [_row("a/main.py", "a", framework=_framework("openai_agents"), evidence=aside)]
    )[0]["agents"][0]["evidence"] == []


def test_clean_name_collapses_every_kind_of_space() -> None:
    assert sd._clean_name("a\u2003\u3000b") == "a b"
    assert sd._clean_name("\u3000a \t\n b\u2028c\u00a0d\u205f ") == "a b c d"
    assert sd._clean_name("a\u200bb\x00c\x1bd\ue000e\ud800f\u202eg\ufeffh") == "abcdefgh"
    assert sd._clean_name("\u3000\u2003\u200b") == ""
    assert _finish([_row("main.py")], root="Shop\u3000\u2003Bot")[0]["agents"][0]["name"] == (
        "Shop Bot"
    )


def test_a_sentence_never_names_a_path_the_path_rule_refuses() -> None:
    undecodable = os.fsdecode(b"agent\xff.py")
    refused = [
        undecodable,
        "../agent.py",
        "/srv/agent.py",
        "my agent.py",
        "a//agent.py",
        "agent.py\n",
        "x" * 513,
        "",
        None,
    ]
    for kind in ("builds langgraph", "graph", "agent_py", "client openai", "project"):
        for path in refused:
            sentence = sd._sentence(kind, path, 3)
            assert sentence is not None
            assert sd._UNNAMED_PATH in sentence
            if path:
                assert path not in sentence
            assert "\udcff" not in sentence
            sentence.encode("utf-8")
            assert len(sentence) <= 200

    long_path = "d/" * 200 + "agent.py"
    sentence = sd._sentence("builds langgraph", long_path, 3)
    assert "…/" in sentence and sentence.endswith("/d/agent.py, line 3.")
    assert len(sentence) <= 200

    one_long_part = "a" * 300 + ".py"
    sentence = sd._sentence("agent_py", one_long_part, 0)
    assert "…" in sentence and len(sentence) <= 200

    assert sd._sentence("client open ai!", "app.py", 1) == (
        "Creates a client for a model provider in app.py, line 1."
    )
    assert sd._sentence("client openai", "app.py", 1) == (
        "Creates a client for openai in app.py, line 1."
    )
    assert sd._sentence("builds \x00", "app.py", 1) == (
        "Builds with an agent framework in app.py, line 1."
    )
    assert sd._sentence("no_such_fact", "app.py", 1) is None


def test_the_file_is_compact_utf8_and_never_fails_on_broken_characters() -> None:
    undecodable = os.fsdecode(b"agent\xff.py")
    rows = [
        _row("main.py", evidence=(("builds langgraph", undecodable, 2),)),
        _row("x" + undecodable, "x" + undecodable),
    ]

    document, body, dropped = _finish(rows, root="Café\ud800 Bot")

    assert body == json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body.decode("utf-8")
    assert "Café Bot".encode("utf-8") in body
    assert b"\\u00e9" not in body
    assert b"\n" not in body
    assert dropped is True
    assert [agent["entry"] for agent in document["agents"]] == ["main.py"]
    assert document["agents"][0]["evidence"] == [
        "Builds with LangGraph in a file in this folder, line 2."
    ]


def test_finish_never_writes_more_than_fifty() -> None:
    rows = [_row(f"f{i:02}/main.py", f"f{i:02}") for i in range(60)]

    document, _, dropped = _finish(rows)

    assert len(document["agents"]) == 50
    assert document["truncated"] is True
    assert dropped is True
    assert [agent["entry"] for agent in document["agents"]] == [
        f"f{i:02}/main.py" for i in range(50)
    ]

    fifty, _, dropped = _finish(rows[:50])
    assert (len(fifty["agents"]), fifty["truncated"], dropped) == (50, False, False)

    first = _row("a/main.py", "a")
    again = _row("a/main.py", "b", found_by_ai=True)
    chosen, left_out = choose_rows([_row("z/main.py", "z"), first, again], limit=50)
    assert [row.candidate.entry for row in chosen] == ["a/main.py", "z/main.py"]
    assert chosen[0] is first
    assert left_out is True
    chosen, left_out = choose_rows(rows, limit=3)
    assert (len(chosen), left_out) == (3, True)
    assert choose_rows(rows[:3]) == (sorted(rows[:3], key=lambda row: row.candidate.entry), False)


def test_the_models_rows_are_chosen_first_only_when_asked() -> None:
    graph = _row("a/graph.py", "a")
    helpers = [_row(f"{name}/main.py", name, sign="client", builds=()) for name in ("b", "c")]
    found = _row("z/agent.ts", "z", found_by_ai=True, builds=())
    in_tests = _row("tests/agent.ts", "tests", side=True, found_by_ai=True, builds=())
    rows = [graph, *helpers, found, in_tests]

    def entries(chosen: list[Row]) -> list[str]:
        return [row.candidate.entry for row in chosen]

    plain, plain_left_out = choose_rows(rows, limit=3)
    first, first_left_out = choose_rows(rows, limit=3, models_first=True)
    many = [_row(f"h{i:02}/main.py", f"h{i:02}", sign="client", builds=()) for i in range(49)]
    body, dropped = finish(
        rows=[graph, *many, found],
        root_name="shop",
        commit_sha=SHA,
        truncated=False,
        error=None,
        models_first=True,
    )
    written = [agent["entry"] for agent in json.loads(body)["agents"]]

    assert _confidence(graph, *helpers, found) == ["high", "medium", "medium", "low"]
    assert (entries(plain), plain_left_out) == (["a/graph.py", "b/main.py", "c/main.py"], True)
    assert (entries(first), first_left_out) == (["a/graph.py", "z/agent.ts", "b/main.py"], True)
    # Which rows are kept changes; the order they are written in does not.
    assert dropped is True
    assert written == ["a/graph.py", *(f"h{i:02}/main.py" for i in range(48)), "z/agent.ts"]


def test_a_row_the_platform_rules_reject_is_dropped_with_truncated(caplog: pytest.LogCaptureFixture) -> None:
    good = _row("good/main.py", "good")
    bad = [
        _row("bad/../escape.py", "bad"),
        _row("label/main.py", "label", framework={"name": "langgraph", "version": None}),
        _row("env/main.py", "env", env_names=("BAD-NAME-sk-fake-123",)),
        _row("tools/main.py", "tools", tools=-1),
        _row("word/main.py", "word", framework=_framework("autogen")),
    ]

    with caplog.at_level(logging.WARNING, logger="fi.alk.harness.scan_document"):
        document, _, dropped = _finish([good, *bad])

    assert [agent["entry"] for agent in document["agents"]] == ["good/main.py"]
    assert document["status"] == "found"
    assert document["truncated"] is True
    assert dropped is True
    left_out = [record.getMessage() for record in caplog.records]
    assert len(left_out) == len(bad)
    assert all(message.startswith("agent scan: row left out, problem=") for message in left_out)
    assert "sk-fake-123" not in caplog.text
    assert "escape" not in caplog.text

    document, _, dropped = _finish(bad)
    assert (document["status"], document["agents"], document["truncated"]) == ("none", [], True)
    assert dropped is True


def test_a_bad_top_level_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def problem(**changes: Any) -> str:
        arguments: dict[str, Any] = {
            "rows": [],
            "root_name": "shop",
            "commit_sha": SHA,
            "truncated": False,
            "error": None,
        }
        arguments.update(changes)
        with pytest.raises(ScanResultInvalid) as raised:
            finish(**arguments)
        assert raised.value.problem in str(raised.value)
        return raised.value.problem

    assert problem(error=("Scan-Failed", "The scan failed.")) == "error.code.pattern"
    assert problem(error=("scan_failed", "  ")) == "error.message.blank"
    assert problem(error=("scan_failed", "m" * 2001)) == "error.message.too_long"
    assert problem(error=("scan_failed", "sk-fake-123\x00")) == "error.message.null_byte"
    assert "sk-fake-123" not in problem(error=("sk-fake-123", "The scan failed."))

    monkeypatch.setattr(sd, "MAX_BYTES", 100)
    assert problem(rows=[_row("main.py")]) == "document.too_large"

    assert wire_problems([]) == ["not_an_object"]
    assert wire_problems({"schema_version": "futureagi.agent-scan.v1"}) == [
        "status.missing",
        "commit_sha.missing",
        "agents.missing",
        "truncated.missing",
        "error.missing",
    ]


def test_the_file_matches_the_platform_serializer() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["serializer_commit"] == "e07d56fcd"
    cases = fixture["cases"]
    assert len(cases) > 100
    assert {case["accepted"] for case in cases} == {True, False}
    # Lone surrogates and null bytes reach the reader only as escapes in the text.
    assert any("\\ud800" in case["document_text"] for case in cases)
    assert any("\\u0000" in case["document_text"] for case in cases)

    disagreements = []
    for case in cases:
        problems = wire_problems(json.loads(case["document_text"]))
        if (problems == []) != case["accepted"]:
            disagreements.append((case["name"], case["accepted"], problems))
        assert all(PROBLEM_NAME.fullmatch(problem) for problem in problems), case["name"]
    assert disagreements == []


def test_a_failed_write_leaves_no_agents_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, body, _ = _finish([_row("main.py")])

    written = write_agents_json(tmp_path / "ok", body)
    assert written == tmp_path / "ok" / "agents.json"
    assert written.read_bytes() == body
    assert stat.S_IMODE(written.stat().st_mode) == 0o644
    assert [path.name for path in (tmp_path / "ok").iterdir()] == ["agents.json"]

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    for name in ("replace", "fsync"):
        output = tmp_path / name
        output.mkdir()
        with monkeypatch.context() as patch:
            patch.setattr(sd.os, name, refuse)
            with pytest.raises(OSError):
                write_agents_json(output, body)
        assert list(output.iterdir()) == []

    earlier = tmp_path / "earlier"
    earlier.mkdir()
    (earlier / "agents.json").write_bytes(b"{}")
    with monkeypatch.context() as patch:
        patch.setattr(sd.os, "replace", refuse)
        with pytest.raises(OSError):
            write_agents_json(earlier, body)
    assert [path.name for path in earlier.iterdir()] == ["agents.json"]
    assert (earlier / "agents.json").read_bytes() == b"{}"


def test_a_disambiguating_suffix_survives_the_255_cut() -> None:
    document, _, _ = _finish([_row("a/main.py"), _row("b/main.py")], root="x" * 300)

    names = sorted(agent["name"] for agent in document["agents"])
    assert names == [
        "x" * (255 - len(" (a/main.py)")) + " (a/main.py)",
        "x" * (255 - len(" (b/main.py)")) + " (b/main.py)",
    ]


def test_a_chat_model_row_is_low_with_its_own_sentence() -> None:
    evidence = (("model langchain", "app/bot.py", 4),)

    agent = _finish(
        [
            _row(
                "app/bot.py",
                "app",
                sign="model",
                builds=(),
                evidence=evidence,
                framework=_framework("langchain"),
            )
        ]
    )[0]["agents"][0]

    assert agent["confidence"] == "low"
    assert agent["evidence"] == ["Calls a chat model through LangChain in app/bot.py, line 4."]
    assert sd._sentence("model no such!", "app.py", 1) == (
        "Calls a chat model through an agent framework in app.py, line 1."
    )
