from fastapi.testclient import TestClient

from fi.alk.harness.sandbox_server import create_app


def test_local_sandbox_rejects_source_outside_allowed_root(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(allowed))
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post("/v1/jobs", json={"source_path": str(outside)})

    assert response.status_code == 403


def test_local_sandbox_persists_and_lists_job_without_secrets(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    monkeypatch.setenv("ALK_SANDBOX_TOKEN", "test-token")
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/jobs",
        headers={"Authorization": "Bearer test-token"},
        json={
            "source_path": str(source),
            "scenario_count": 7,
            "secret_refs": {
                "livekit": {
                    "manager": "environment",
                    "key": "LK_URL",
                    "purpose": "livekit_url",
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["total_scenarios"] == 7
    assert body["job"]["agent"]["secret_refs"]["livekit"]["key"] == "LK_URL"
    assert client.get(
        "/v1/jobs", headers={"Authorization": "Bearer test-token"}
    ).json()[0]["job"]["job_id"] == body["job"]["job_id"]
