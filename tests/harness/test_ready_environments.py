"""The ready environments a release ships (`fi.alk.environments.ready`): which worlds they are,
that each archive packs to the same bytes from the same source, and what the platform reads from
an archive and the list. Sealing on PostgreSQL runs when `ALK_RELEASE_POSTGRES_DSN` is set.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import ready
from fi.alk.harness.releases import ReleaseError

HERE = Path(environments.__file__).parent
DSN = os.environ.get("ALK_RELEASE_POSTGRES_DSN", "")


def _vertical_packages() -> list[str]:
    """The packages of the README's "Vertical environments" table, less the clinic reference."""
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    table = readme.split("## Vertical environments", 1)[1].split("\n## ", 1)[0]
    return sorted(
        name
        for name in re.findall(r"^\| `([a-z_]+)` \|", table, flags=re.MULTILINE)
        if name != "appointment_booking"
    )


def _sealed(root: Path) -> Path:
    (root / "environment/handlers").mkdir(parents=True)
    (root / "environment/release.json").write_text("{}")
    (root / "environment/handlers/get_order.py").write_text("def handle(): ...")
    (root / "suite/scenarios/refund").mkdir(parents=True)
    (root / "suite/suite.json").write_text("{}")
    (root / "suite/scenarios/refund/scenario.json").write_text("{}")
    return root


def test_the_ready_environments_are_the_readme_verticals():
    assert sorted(ready.READY_ENVIRONMENTS) == _vertical_packages()
    for name in ready.READY_ENVIRONMENTS:
        assert (HERE / name / "build.py").is_file()


def test_an_archive_packs_to_the_same_bytes(tmp_path):
    first = ready.pack(_sealed(tmp_path / "a"))
    second = ready.pack(_sealed(tmp_path / "b"))

    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
        members = archive.getmembers()
    assert [member.name for member in members] == [
        "environment/handlers/get_order.py",
        "environment/release.json",
        "suite/scenarios/refund/scenario.json",
        "suite/suite.json",
    ]
    assert all(member.isfile() and member.mtime == 0 for member in members)
    assert {member.mode for member in members} == {0o644}


def test_a_symlink_is_never_packed(tmp_path):
    root = _sealed(tmp_path)
    (root / "suite/link").symlink_to(root / "suite/suite.json")

    with pytest.raises(ReleaseError) as refused:
        ready.pack(root)

    assert refused.value.code == "ready_environment_invalid"


def test_publishing_needs_postgres(tmp_path):
    with pytest.raises(ReleaseError) as refused:
        ready.publish(tmp_path, postgres_dsn="", version="1.0.0", revision="abc")

    assert refused.value.code == "ready_environment_unproved"


def test_publishing_needs_an_empty_folder(tmp_path):
    (tmp_path / "left-over").write_text("x")

    with pytest.raises(ReleaseError) as refused:
        ready.publish(
            tmp_path, postgres_dsn="postgresql://x", version="1", revision="a"
        )

    assert refused.value.code == "ready_environment_invalid"


def test_only_a_ready_environment_is_sealed(tmp_path):
    with pytest.raises(ReleaseError) as refused:
        ready.seal("gmail_inbox_triage", tmp_path, postgres_dsn="postgresql://x")

    assert refused.value.code == "ready_environment_unknown"


def test_the_command_line_refuses_an_unknown_world(tmp_path, capsys):
    with pytest.raises(SystemExit):
        ready.main(["--out", str(tmp_path), "--world", "nowhere"])


@pytest.mark.skipif(not DSN, reason="set ALK_RELEASE_POSTGRES_DSN")
def test_a_published_world_is_what_the_list_names(tmp_path):
    out = tmp_path / "out"

    manifest = ready.publish(
        out, postgres_dsn=DSN, version="1.2.3", revision="abc", worlds=("retail",)
    )

    assert manifest == json.loads((out / ready.MANIFEST).read_text())
    assert manifest["schema_version"] == ready.SCHEMA
    assert (manifest["alk_version"], manifest["alk_source_revision"]) == (
        "1.2.3",
        "abc",
    )
    [entry] = manifest["environments"]
    body = (out / entry["archive"]).read_bytes()
    assert entry["archive"] == "ready-environment-retail.tar.gz"
    assert (entry["sha256"], entry["size"]) == (
        hashlib.sha256(body).hexdigest(),
        len(body),
    )
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        release = json.load(archive.extractfile("environment/release.json"))
        suite = json.load(archive.extractfile("suite/suite.json"))
        proofs = json.load(archive.extractfile("suite/proofs.json"))
        parts = {member.name.split("/", 1)[0] for member in archive.getmembers()}
    assert parts == {"environment", "suite"}
    assert (release["digest"], suite["digest"]) == (
        entry["environment"],
        entry["suite"],
    )
    assert suite["environment"]["digest"] == release["digest"]
    assert release["metadata"]["catalogue"]["title"]
    assert proofs["runtime"]["engine"] == "postgres"

    # The same source packs to the same archive, so an unchanged world is not registered again.
    again = ready.publish(
        tmp_path / "again",
        postgres_dsn=DSN,
        version="1.2.3",
        revision="abc",
        worlds=("retail",),
    )
    assert again == manifest
