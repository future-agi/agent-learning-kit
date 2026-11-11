"""The harness's own spend, and that every session feeds it."""

from __future__ import annotations

import json

import pytest

from fi.alk.harness import spend
from fi.alk.harness.backends import SessionSpec, StageDone
from fi.alk.harness.session import Stage, Turn


def _fresh(tmp_path):
    spend._stages.clear()
    spend.journal_to(tmp_path / "cost.json")
    return tmp_path / "cost.json"


def test_the_total_is_every_stage_added_up(tmp_path):
    journal = _fresh(tmp_path)
    spend.record("understand-agent", 0.0123, turns=4, models={"m"})
    spend.record("write-scenarios:slice-a", 0.5, turns=30, models={"m"})
    spend.record("write-scenarios:slice-b", 0.25, turns=20, models={"m"})

    body = json.loads(journal.read_text(encoding="utf-8"))
    assert body["total_usd"] == 0.7623
    assert [entry["stage"] for entry in body["stages"]] == [
        "understand-agent",
        "write-scenarios:slice-a",
        "write-scenarios:slice-b",
    ]


def test_a_turn_nobody_could_price_is_reported_rather_than_dropped(tmp_path):
    """An unpriced turn makes the total a floor, and a floor presented as a total is a wrong bill."""
    journal = _fresh(tmp_path)
    spend.record("build-environment", 0.02, turns=3, models={"m"})
    spend.record("build-environment", None, turns=1, models={"m"})

    body = json.loads(journal.read_text(encoding="utf-8"))
    assert body["total_usd"] == 0.02
    assert body["unpriced_turns"] == 1


def test_the_file_is_current_after_every_turn(tmp_path):
    """The platform reads this while the sandbox lives, so it cannot be written only at the end."""
    journal = _fresh(tmp_path)
    spend.record("understand-agent", 0.1, turns=1)
    assert json.loads(journal.read_text(encoding="utf-8"))["total_usd"] == 0.1
    spend.record("understand-agent", 0.2, turns=1)
    assert json.loads(journal.read_text(encoding="utf-8"))["total_usd"] == 0.3


def test_a_session_reports_its_own_cost_without_being_asked(tmp_path):
    """The hook is in `Stage`, so a stage added later is counted with no new call site."""
    journal = _fresh(tmp_path)
    stage = Stage(SessionSpec(system_prompt="x"), name="write-scenarios:slice-c")
    stage._events(
        StageDone(outcome="success", turns=7, cost_usd=0.4, models={"gemini"}),
        Turn(),
    )

    body = json.loads(journal.read_text(encoding="utf-8"))
    assert body["total_usd"] == 0.4
    assert body["stages"][0]["stage"] == "write-scenarios:slice-c"
    assert body["stages"][0]["turns"] == 7
    assert body["stages"][0]["models"] == ["gemini"]


def test_a_turn_billed_to_a_forbidden_model_stops_the_stage(tmp_path):
    _fresh(tmp_path)
    stage = Stage(SessionSpec(system_prompt="x"), name="write-scenarios")
    with pytest.raises(RuntimeError, match="may not spend on: claude-opus-4-7"):
        stage._events(StageDone(models={"claude-opus-4-7"}), Turn())


def test_the_tokens_behind_a_price_are_kept(tmp_path):
    """A dollar figure with no units cannot be audited, and this one is a bill."""
    journal = _fresh(tmp_path)
    spend.record("understand-agent", 0.02, turns=2, models={"m"}, tokens_in=5000, tokens_out=1200)
    spend.record("understand-agent", 0.01, turns=1, models={"m"}, tokens_in=2000, tokens_out=300)

    entry = json.loads(journal.read_text(encoding="utf-8"))["stages"][0]
    assert entry["tokens_in"] == 7000
    assert entry["tokens_out"] == 1500
    assert entry["usd"] == 0.03


def test_a_session_reports_its_tokens_too(tmp_path):
    journal = _fresh(tmp_path)
    stage = Stage(SessionSpec(system_prompt="x"), name="build-environment")
    stage._events(
        StageDone(
            outcome="success",
            turns=3,
            cost_usd=0.5,
            models={"gemini"},
            tokens_in=9000,
            tokens_out=2500,
        ),
        Turn(),
    )

    entry = json.loads(journal.read_text(encoding="utf-8"))["stages"][0]
    assert (entry["tokens_in"], entry["tokens_out"]) == (9000, 2500)


def _on(monkeypatch, day):
    """Price as if today were `day`; the table is a schedule, so a price depends on the date."""
    from fi.alk.harness.backends import vertex_gemini

    monkeypatch.setattr(vertex_gemini, "_today", lambda: day)


def test_the_price_day_is_the_utc_day(monkeypatch):
    """The platform's dated rows switch at midnight UTC, so the kit's do too (Dileep, #274)."""
    from datetime import datetime, timezone

    from fi.alk.harness.backends import vertex_gemini

    def at(moment):
        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                assert tz is timezone.utc
                return moment

        monkeypatch.setattr(vertex_gemini, "datetime", _Clock)

    at(datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc))
    assert vertex_gemini.current_rates("gemini-3.7-flash") == (0.75, 3.75)
    at(datetime(2027, 1, 1, 0, 30, tzinfo=timezone.utc))
    assert vertex_gemini.current_rates("gemini-3.7-flash") == (1.50, 7.50)


@pytest.mark.parametrize("model", ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash"])
def test_a_price_past_its_re_check_date_keeps_billing_the_latest_row(monkeypatch, caplog, model):
    """The Flash models double on 2027-01-01, as Google's Vertex pricing page says
    (cloud.google.com/model-garden/pricing, Global tier, read 2026-11-10). Past the table's
    re-check date a running release keeps the latest row's price and warns, rather than leaving
    every turn unpriced."""
    import logging
    from datetime import date

    from fi.alk.harness.backends import vertex_gemini

    monkeypatch.setattr(vertex_gemini, "_STALE_WARNED", set())
    caplog.set_level(logging.WARNING, logger=vertex_gemini.__name__)

    _on(monkeypatch, date(2026, 11, 2))
    assert vertex_gemini.priced(model, 1_000_000, 1_000_000) == 4.5

    _on(monkeypatch, date(2026, 12, 31))
    assert vertex_gemini.priced(model, 1_000_000, 1_000_000) == 4.5
    assert vertex_gemini.priced(model, 1_000_000, 0, 1_000_000) == pytest.approx(0.075)

    _on(monkeypatch, date(2027, 3, 31))
    assert vertex_gemini.priced(model, 1_000_000, 1_000_000) == 9.0
    assert vertex_gemini.priced(model, 1_000_000, 0, 1_000_000) == pytest.approx(0.15)
    assert caplog.records == []

    _on(monkeypatch, date(2027, 4, 1))
    assert vertex_gemini.priced(model, 1_000_000, 1_000_000) == 9.0
    assert vertex_gemini.priced(model, 1_000_000, 0, 1_000_000) == pytest.approx(0.15)
    stale = [r for r in caplog.records if "past its re-check date" in r.getMessage()]
    # Once a day per model, not once a turn.
    assert len(stale) == 1 and stale[0].levelno == logging.WARNING
    assert model in stale[0].getMessage() and vertex_gemini.CHECK_BY in stale[0].getMessage()

    _on(monkeypatch, date(2027, 4, 2))
    vertex_gemini.priced(model, 1_000, 1_000)
    assert len([r for r in caplog.records if "past its re-check date" in r.getMessage()]) == 2


def test_a_model_with_no_row_stays_unpriced(monkeypatch, caplog):
    """No price is guessed where the table has none: before a model's first row, or for a model
    it doesn't list. Those turns are counted in `unpriced_turns`."""
    import logging
    from datetime import date

    from fi.alk.harness.backends import vertex_gemini

    monkeypatch.setattr(vertex_gemini, "_STALE_WARNED", set())
    caplog.set_level(logging.WARNING, logger=vertex_gemini.__name__)
    _on(monkeypatch, date(2026, 11, 1))
    assert vertex_gemini.priced("gemini-3.7-flash", 1_000_000, 1_000_000) is None
    assert "no current price for gemini-3.7-flash" in caplog.text
    assert "past its re-check date" not in caplog.text
    _on(monkeypatch, date(2027, 4, 1))
    assert vertex_gemini.priced("a-model-nobody-listed", 1_000, 1_000) is None
    assert "past its re-check date" not in caplog.text


def test_every_model_s_rows_follow_on_and_end_on_the_check_by_date():
    """Runs on every PR: the table's shape, which no date can make red."""
    from datetime import date, timedelta

    from fi.alk.harness.backends.vertex_gemini import CHECK_BY, PRICES_PER_MILLION

    for name, rows in PRICES_PER_MILLION.items():
        dated = sorted(rows, key=lambda row: row[2])
        assert dated[-1][3] == CHECK_BY, f"{name}'s latest row is not re-checked by {CHECK_BY}"
        for row in dated:
            assert row[2] <= row[3], f"{name} has a row that ends before it starts: {row}"
        for earlier, later in zip(dated, dated[1:]):
            # A gap is a day nothing is priced; an overlap is a day priced twice.
            assert date.fromisoformat(later[2]) == date.fromisoformat(earlier[3]) + timedelta(
                days=1
            ), f"{name}'s rows do not follow on: {earlier} then {later}"


# The weekly price-check-by workflow sets this; test_the_weekly_workflow_runs_the_check_by_test
# pins the two ends together. pytest evaluates the skipif string with `os` in scope.
CHECK_BY_ENV = "ALK_PRICE_CHECK_BY"


@pytest.mark.skipif(
    "not os.environ.get(CHECK_BY_ENV)",
    reason="runs from the weekly price-check-by workflow, so the date never blocks a PR",
)
def test_the_price_table_is_re_checked_before_its_check_by_date():
    """Red from a month before CHECK_BY (2027-03-01 for 2027-03-31), on the real UTC day, until
    each model is re-checked against the gateway table and Google and CHECK_BY moves on."""
    from datetime import date

    from fi.alk.harness.backends import vertex_gemini

    assert (date.fromisoformat(vertex_gemini.CHECK_BY) - vertex_gemini._today()).days > 30, (
        f"the price table must be re-checked by {vertex_gemini.CHECK_BY}: compare every row with "
        "the gateway's litellm.json and Google's Vertex pricing page, then move CHECK_BY on"
    )


def test_the_check_by_date_test_goes_red_from_a_month_before(monkeypatch):
    """The scheduled test's own boundary, checked on every PR with fixed days."""
    from datetime import date, timedelta

    from fi.alk.harness.backends import vertex_gemini

    check_by = date.fromisoformat(vertex_gemini.CHECK_BY)
    _on(monkeypatch, check_by - timedelta(days=31))
    test_the_price_table_is_re_checked_before_its_check_by_date()
    _on(monkeypatch, check_by - timedelta(days=30))
    with pytest.raises(AssertionError, match=f"must be re-checked by {check_by.isoformat()}"):
        test_the_price_table_is_re_checked_before_its_check_by_date()


def test_the_weekly_workflow_runs_the_check_by_test():
    """The scheduled job and the test's skip share one env var and one node id, the job fails on a
    skip, and a failure reaches the team: any slip here leaves the reminder green or turns every PR
    red."""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "price-check-by.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"] == [{"cron": "0 6 * * 1"}]
    steps = workflow["jobs"]["check-by"]["steps"]
    check = next(step for step in steps if step.get("env", {}).get(CHECK_BY_ENV))
    node = "test_spend.py::" + test_the_price_table_is_re_checked_before_its_check_by_date.__name__
    assert node in check["run"]
    # A skip there must fail the job, not pass it.
    assert "1 passed" in check["run"] and "pipefail" in check["run"]
    assert all("continue-on-error" not in step for step in steps)
    assert "continue-on-error" not in workflow["jobs"]["check-by"]
    # Only the notify job may write issues, and it installs nothing.
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["check-by"]
    notify = workflow["jobs"]["notify"]
    assert notify["needs"] == "check-by"
    assert notify["if"] == "failure() && github.event_name == 'schedule'"
    assert notify["permissions"] == {"issues": "write"}
    assert not any("uses" in step or "pip" in step.get("run", "") for step in notify["steps"])

    marks = test_the_price_table_is_re_checked_before_its_check_by_date.pytestmark
    skip = next(mark for mark in marks if mark.name == "skipif")
    # The skip reads the same name the workflow sets, and nothing else.
    assert skip.args == ("not os.environ.get(CHECK_BY_ENV)",)


def test_the_rate_is_the_row_whose_dates_hold_today(monkeypatch):
    """The row is chosen by its dates, never by its place in the table."""
    from datetime import date

    from fi.alk.harness.backends import vertex_gemini
    from fi.alk.harness.backends.vertex_gemini import current_rates

    rows = vertex_gemini.PRICES_PER_MILLION["gemini-3.7-flash"]
    newest_first = tuple(sorted(rows, key=lambda row: row[2], reverse=True))
    monkeypatch.setitem(vertex_gemini.PRICES_PER_MILLION, "gemini-3.7-flash", newest_first)

    assert current_rates("gemini-3.7-flash", date(2026, 12, 31)) == (0.75, 3.75)
    assert current_rates("gemini-3.7-flash", date(2027, 1, 1)) == (1.50, 7.50)
    assert current_rates("gemini-3.7-flash", date(2026, 11, 1)) is None
    assert current_rates("vertex_ai/gemini-3.7-flash", date(2027, 1, 1)) == (1.50, 7.50)
    assert current_rates("a-model-nobody-listed", date(2027, 1, 1)) is None

    _on(monkeypatch, date(2026, 12, 31))
    assert vertex_gemini.priced("gemini-3.7-flash", 1_000_000, 1_000_000) == 4.5
    _on(monkeypatch, date(2027, 1, 1))
    assert vertex_gemini.priced("gemini-3.7-flash", 1_000_000, 1_000_000) == 9.0
    _on(monkeypatch, date(2026, 11, 1))
    assert vertex_gemini.priced("gemini-3.7-flash", 1_000_000, 1_000_000) is None


def test_a_two_call_gemini_stage_records_the_plain_sum(tmp_path, monkeypatch):
    """Gemini reports each call on its own, so its ledger is the calls added up, unchanged by
    the Claude backend's running-total handling."""
    from datetime import date

    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from fi.alk.harness.backends import vertex_gemini
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiBackend

    calls = iter([(1_000, 100, 200), (3_000, 300, 600)])

    async def generate(self, llm_request, stream=False):
        prompt, candidates, cached = next(calls)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="ok")]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=prompt,
                candidates_token_count=candidates,
                cached_content_token_count=cached,
            ),
        )

    monkeypatch.setattr(Gemini, "generate_content_async", generate)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    _on(monkeypatch, date(2026, 12, 1))
    journal = _fresh(tmp_path)

    async def run():
        async with Stage(
            SessionSpec(system_prompt="Reply briefly", max_turns=1, model="gemini-3.7-flash"),
            name="build-environment",
            backend=VertexGeminiBackend(),
            overheard=False,
        ) as stage:
            await stage.say("one")
            await stage.say("two")

    import asyncio

    asyncio.run(run())

    entry = json.loads(journal.read_text(encoding="utf-8"))["stages"][0]
    assert (entry["tokens_in"], entry["tokens_out"], entry["tokens_cached"]) == (4_000, 400, 800)
    assert entry["usd"] == pytest.approx(
        vertex_gemini.priced("gemini-3.7-flash", 1_000, 100, 200)
        + vertex_gemini.priced("gemini-3.7-flash", 3_000, 300, 600)
    )
    assert entry["unpriced"] == 0


def test_both_backends_report_the_same_units():
    """One backend giving auditable tokens and the other not is a ledger you cannot reconcile."""
    from fi.alk.harness.backends.claude import _tokens

    assert _tokens({"m": {"inputTokens": 900, "outputTokens": 120}}) == {
        "tokens_in": 900,
        "tokens_out": 120,
        "tokens_cached": 0,
    }
    assert _tokens(
        {"a": {"input_tokens": 5, "output_tokens": 6}, "b": {"input_tokens": 7, "output_tokens": 8}}
    ) == {"tokens_in": 12, "tokens_out": 14, "tokens_cached": 0}
    assert _tokens(None) == {"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0}
    assert _tokens({"m": object()}) == {"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0}
    # Cache reads arrive beside fresh input, the way the Messages API reports them.
    assert _tokens(
        {"m": {"inputTokens": 40, "outputTokens": 10, "cacheReadInputTokens": 9_000}}
    ) == {"tokens_in": 9_040, "tokens_out": 10, "tokens_cached": 9_000}


def test_the_ledger_carries_input_the_provider_served_from_cache(tmp_path):
    """Two reruns of the same authoring produced byte-identical ledgers ($3.533797, identical
    token counts) four times apart in wall clock. That is what provider caching looks like when
    nothing records it, and it means a rerun's ledger overstates what was actually billed."""
    spend._stages.clear()
    spend.record("understand-agent", 1.0, turns=2, tokens_in=1000, tokens_out=50, tokens_cached=800)
    spend.record("understand-agent", 1.0, turns=1, tokens_in=500, tokens_out=25, tokens_cached=400)

    stage = spend.snapshot()["stages"][0]
    assert stage["tokens_in"] == 1500
    assert stage["tokens_cached"] == 1200, "the cached share has to be visible to be reconciled"
    assert stage["tokens_out"] == 75


def test_cached_tokens_default_to_zero_for_a_backend_that_does_not_report_them(tmp_path):
    spend._stages.clear()
    spend.record("write-scenarios", 0.5, turns=1, tokens_in=100, tokens_out=10)

    assert spend.snapshot()["stages"][0]["tokens_cached"] == 0


def test_the_default_model_is_one_we_can_price():
    """A run on an unpriced model reports a total that understates the bill."""
    from fi.alk.harness.backends.vertex_gemini import DEFAULT_MODEL, PRICES_PER_MILLION

    assert DEFAULT_MODEL in PRICES_PER_MILLION, (
        f"{DEFAULT_MODEL} has no entry in PRICES_PER_MILLION, so every run on it reports "
        "less than it cost"
    )


def test_every_harness_stage_feeds_the_one_ledger():
    """Parallel scenario writers and the suite review each open their own session."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "fi" / "alk" / "harness"
    # rglob, not glob: a second call site added under backends/ or world/ has to fail this too.
    recorded = sorted(
        path.name
        for path in root.rglob("*.py")
        if "spend.record(" in path.read_text(encoding="utf-8")
    )
    assert recorded == ["session.py"], (
        "spend must be recorded in exactly one place; a second call site double-counts or drifts, "
        f"found: {recorded}"
    )


def test_todays_prices_match_the_gateway_table_as_committed():
    """Runs in CI, unlike the live comparison below: the gateway's rows at 9ec5f9fb1, committed as
    a fixture, so a mistyped price fails here (Dileep, #274)."""
    import json
    from datetime import date
    from pathlib import Path

    from fi.alk.harness.backends.vertex_gemini import PRICES_PER_MILLION, current_rates

    fixture = Path(__file__).parent / "fixtures" / "litellm_vertex_prices.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))["rows"]
    assert sorted(rows) == sorted(f"vertex_ai/{name}" for name in PRICES_PER_MILLION)
    for name in PRICES_PER_MILLION:
        row = rows[f"vertex_ai/{name}"]
        assert current_rates(name, date(2026, 12, 1)) == (
            round(row["input_cost_per_token"] * 1_000_000, 6),
            round(row["output_cost_per_token"] * 1_000_000, 6),
        ), name


def test_the_price_table_agrees_with_the_platform_model_table():
    """Prices are copied from the platform's litellm table, so drift is a silent mis-bill.

    From 2027-01-01 this is red for the three Flash models until the gateway's table carries
    their 2027 rows too (TH-9508): both tables must change together."""
    import json
    from pathlib import Path

    from fi.alk.harness.backends.vertex_gemini import PRICES_PER_MILLION, current_rates

    # The platform checked out beside this repo, or nowhere: never an absolute path, which would
    # carry one machine's layout into a public repo and skip for everyone else.
    table = (
        Path(__file__).resolve().parents[3]
        / "future-agi"
        / "agentcc-gateway"
        / "internal"
        / "modeldb"
        / "litellm.json"
    )
    if not table.exists():
        import pytest

        pytest.skip("platform checkout not beside this repo")

    models = json.loads(table.read_text(encoding="utf-8"))
    # Today's rates only: the gateway's table holds no schedule (TH-9508 adds the 2027 change).
    for name in PRICES_PER_MILLION:
        rates = current_rates(name)
        assert rates, f"{name} has no price for today"
        want_in, want_out = rates
        entry = models.get(f"vertex_ai/{name}") or models.get(name)
        assert entry, f"{name} is priced here but absent from the platform table"
        assert round(entry["input_cost_per_token"] * 1_000_000, 6) == want_in, name
        assert round(entry["output_cost_per_token"] * 1_000_000, 6) == want_out, name


def test_a_gateway_route_in_front_of_the_model_does_not_lose_its_price(monkeypatch):
    from datetime import date

    from fi.alk.harness.backends import vertex_gemini

    _on(monkeypatch, date(2026, 12, 1))
    bare = vertex_gemini.priced("gemini-3.7-flash", 1_000_000, 1_000_000)
    assert bare is not None
    assert vertex_gemini.priced("vertex_ai/gemini-3.7-flash", 1_000_000, 1_000_000) == bare
    assert vertex_gemini.priced("vertex_ai/a-model-nobody-listed", 1_000, 1_000) is None
