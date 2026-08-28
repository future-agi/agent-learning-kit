from __future__ import annotations

import json

from fi.alk.harness import hosted_authoring_entrypoint as entrypoint


def test_vertex_generation_region_is_not_copied_from_google_location(
    tmp_path, monkeypatch
) -> None:
    adc = json.dumps({"type": "service_account", "project_id": "p"})
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "google.json")
    monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
    entrypoint._configure_generation_environment(
        {
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": adc,
            "GOOGLE_CLOUD_PROJECT": "p",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        }
    )
    assert entrypoint.os.environ["CLOUD_ML_REGION"] == "us-east5"
    assert entrypoint.os.environ["ANTHROPIC_VERTEX_PROJECT_ID"] == "p"


def test_explicit_claude_vertex_region_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "google.json")
    monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
    entrypoint._configure_generation_environment(
        {
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": json.dumps({"type": "service_account"}),
            "GOOGLE_CLOUD_PROJECT": "p",
            "ANTHROPIC_VERTEX_REGION": "europe-west1",
        }
    )
    assert entrypoint.os.environ["CLOUD_ML_REGION"] == "europe-west1"
