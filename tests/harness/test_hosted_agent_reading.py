from __future__ import annotations

import json
from pathlib import Path

import pytest

from fi.alk.harness import cli, hosted_agent_reading, hosted_authoring_entrypoint, spend
from fi.alk.harness.contract import AgentContract


def _contract() -> str:
    return AgentContract.model_validate(
        {
            "agent": "shop-agent",
            "modality": "chat",
            "tools": [{"name": "send_email", "args": ["to", "body"]}],
        }
    ).model_dump_json()


@pytest.fixture
def reading(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        hosted_authoring_entrypoint, "configure_authoring_credentials", lambda: {}
    )

    def understand(argv, contract=_contract(), status=0):
        calls.append(argv)
        out = Path(argv[argv.index("--out") + 1])
        if contract is not None:
            (out / "contract.json").write_text(contract, encoding="utf-8")
        return status

    monkeypatch.setattr(cli, "main", understand)
    return calls, tmp_path / "reading"


def test_reading_writes_the_agent_document_from_the_contract(reading, tmp_path):
    calls, output = reading

    status = hosted_agent_reading.main(
        ["--source", str(tmp_path), "--output", str(output), "--name", "shop"]
    )

    assert status == 0
    assert calls[0][:3] == ["understand", "--name", "shop"]
    assert "--once" in calls[0]
    document = json.loads((output / "agent.json").read_text())
    assert document["sdk_version"]
    assert [tool["name"] for tool in document["agent"]["tools"]] == ["send_email"]
    assert set(document["agent"]["tools"][0]["parameters"]["properties"]) == {
        "to",
        "body",
    }
    assert document["agent"]["kind"] == "source"


def test_a_failed_understand_leaves_no_document_and_exits_zero(
    monkeypatch, reading, tmp_path, capsys
):
    _, output = reading
    monkeypatch.setattr(cli, "main", lambda argv: 1)

    status = hosted_agent_reading.main(
        ["--source", str(tmp_path), "--output", str(output)]
    )

    assert status == 0
    assert not (output / "agent.json").exists()
    assert "agent reading failed" in capsys.readouterr().err


def test_an_unreadable_contract_leaves_no_document(monkeypatch, reading, tmp_path):
    _, output = reading

    def broken(argv):
        out = Path(argv[argv.index("--out") + 1])
        (out / "contract.json").write_text("{not json", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "main", broken)

    assert (
        hosted_agent_reading.main(["--source", str(tmp_path), "--output", str(output)])
        == 0
    )
    assert not (output / "agent.json").exists()


def test_the_reading_spend_goes_to_the_named_journal(monkeypatch, reading, tmp_path):
    _, output = reading
    journal = tmp_path / "cost.json"
    monkeypatch.setenv(spend.JOURNAL_ALIAS, str(journal))
    seen: list[object] = []
    monkeypatch.setattr(spend, "journal_to", lambda path: seen.append(path))

    hosted_agent_reading.main(["--source", str(tmp_path), "--output", str(output)])

    assert seen == [str(journal)]


def test_credentials_are_configured_before_understand(monkeypatch, reading, tmp_path):
    _, output = reading
    order: list[str] = []
    monkeypatch.setattr(
        hosted_authoring_entrypoint,
        "configure_authoring_credentials",
        lambda: order.append("credentials") or {},
    )
    monkeypatch.setattr(cli, "main", lambda argv: order.append("understand") or 1)

    hosted_agent_reading.main(["--source", str(tmp_path), "--output", str(output)])

    assert order == ["credentials", "understand"]


def test_a_failed_understand_with_a_partial_contract_writes_nothing(
    monkeypatch, reading, tmp_path
):
    _, output = reading

    def partial(argv):
        out = Path(argv[argv.index("--out") + 1])
        (out / "contract.json").write_text(_contract(), encoding="utf-8")
        return 1

    monkeypatch.setattr(cli, "main", partial)

    hosted_agent_reading.main(["--source", str(tmp_path), "--output", str(output)])

    assert not (output / "agent.json").exists()


def test_a_failed_reading_removes_an_earlier_document(monkeypatch, reading, tmp_path):
    _, output = reading
    output.mkdir()
    (output / "agent.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "main", lambda argv: 1)

    hosted_agent_reading.main(["--source", str(tmp_path), "--output", str(output)])

    assert not (output / "agent.json").exists()
