from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from fi.alk.harness.design import parse
from fi.alk.harness.design.documents import _replaced
from fi.alk.harness.design.tools import RESULT, DesignTools


def test_numeric_personal_details_are_replaced_without_coercing_unmatched_values() -> (
    None
):
    matched: set[str] = set()

    replaced, count = _replaced(919876543210, {"919876543210": "5551234567"}, matched)
    untouched, untouched_count = _replaced(42, {"919876543210": "5551234567"}, matched)

    assert (replaced, count) == ("5551234567", 1)
    assert (untouched, untouched_count) == (42, 0)
    assert matched == {"919876543210"}


def test_normalized_document_part_names_do_not_overwrite_each_other(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "book.xlsx"
    source.write_bytes(b"unused")
    out = tmp_path / "parsed"
    monkeypatch.setattr(
        parse,
        "parse",
        lambda _source: (
            "xlsx",
            [
                (
                    "A B",
                    {"columns": ["id"], "rows": [[1]], "truncated": False},
                ),
                (
                    "A-B",
                    {"columns": ["id"], "rows": [[2]], "truncated": False},
                ),
            ],
        ),
    )

    assert parse.main([str(source), str(out)]) == 0

    listed = json.loads((out / "parts.json").read_text(encoding="utf-8"))
    assert [(one["name"], one["title"]) for one in listed["parts"]] == [
        ("A-B", "A B"),
        ("A-B-2", "A-B"),
    ]
    assert json.loads((out / "A-B.json").read_text(encoding="utf-8"))["rows"] == [[1]]
    assert json.loads((out / "A-B-2.json").read_text(encoding="utf-8"))["rows"] == [[2]]


def test_concurrent_finish_and_stop_finalize_a_design_once(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    output = tmp_path / "output"
    workspace.mkdir()
    tools = DesignTools.__new__(DesignTools)
    tools.workspace = workspace
    tools.output = output
    tools.done = False
    tools._finishing = asyncio.Lock()
    tools._not_ready = lambda: ""
    seals = 0

    def seal(_out: Path) -> tuple[SimpleNamespace, SimpleNamespace]:
        nonlocal seals
        seals += 1
        time.sleep(0.05)
        return SimpleNamespace(
            name="desk", digest="sha256:environment"
        ), SimpleNamespace(name="suite", digest="sha256:suite", scenarios=[object()])

    tools._seal = seal

    async def finalize() -> list[dict]:
        return await asyncio.gather(
            tools.finish_design({}),
            tools.finish_design({}),
            tools.stop_design({"reason": "cancelled"}),
        )

    results = asyncio.run(finalize())

    assert seals == 1
    assert (
        json.loads((output / RESULT).read_text(encoding="utf-8"))["status"]
        == "completed"
    )
    assert all("is_error" not in result for result in results)
