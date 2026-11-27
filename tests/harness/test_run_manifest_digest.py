"""A run manifest keeps the digest it was sealed with, whichever fields its writer knew."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fi.alk.harness.bundle_v2 import _canonical_json
from fi.alk.harness.releases import (
    ReleaseError,
    RunManifestV1,
    load_run_manifest,
    seal_run_manifest,
)

SHA = "sha256:" + "a" * 64


def _fields(**llm) -> dict:
    return {
        "environment": [{"name": "shop", "digest": SHA}],
        "suite": {"digest": SHA},
        "binding": {"digest": SHA},
        "agent": {
            "agent_definition_id": "agent",
            "agent_version_id": "version",
            "configuration_digest": SHA,
            "connector": "retell_chat",
            "agent_config_digest": SHA,
            "provider_target_id": "target",
            "provider_digest": SHA,
        },
        "selection": {"scenario_keys": ["one"], "trials": 1},
        "simulator": {"llm": {"provider": "vertex", "model": "m", **llm}},
        "scorer": {"alk_version": "1"},
        "runtime": {"provider": "e2b", "image_name": "i", "image_digest": "d"},
    }


def _written(path: Path, **llm) -> dict:
    """A manifest as its writer put it, its digest over exactly the fields it holds."""
    model = RunManifestV1.model_validate(
        {**_fields(**llm), "schema_version": "futureagi.run-manifest.v1", "digest": SHA}
    )
    document = model.model_dump(mode="json")
    del document["digest"], document["binding_proof"]
    document["simulator"]["llm"] = {
        **{key: None for key in ("location", "thinking")},
        "provider": "vertex",
        "model": "m",
        **llm,
    }
    document["digest"] = "sha256:" + hashlib.sha256(_canonical_json(document)).hexdigest()
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


@pytest.mark.parametrize(
    "llm",
    [{}, {"fallback_models": []}, {"fallback_models": ["spare"]}],
    ids=["before the field", "none", "one"],
)
def test_a_manifest_loads_under_the_digest_it_was_written_with(tmp_path, llm) -> None:
    path = tmp_path / "manifest.json"
    written = _written(path, **llm)

    manifest = load_run_manifest(path)

    assert manifest.digest == written["digest"]
    assert manifest.simulator.llm.fallback_models == llm.get("fallback_models", [])
    # What is sealed and written out again is still that manifest.
    assert seal_run_manifest(_fields(**llm)).digest == written["digest"]
    path.write_text(json.dumps(manifest.model_dump(mode="json")), encoding="utf-8")
    assert load_run_manifest(path).digest == written["digest"]


def test_adding_a_fallback_to_a_manifest_changes_its_digest(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    document = _written(path)
    document["simulator"]["llm"]["fallback_models"] = ["other"]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReleaseError, match="run_manifest_digest_mismatch"):
        load_run_manifest(path)
