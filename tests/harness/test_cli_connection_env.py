from fi.alk.harness.cli import _declared_connection_env_names
from fi.alk.harness.provision import _contract_runtime_configuration_names
from types import SimpleNamespace


def test_local_connection_names_survive_without_persisting_values(tmp_path):
    (tmp_path / ".env.local").write_text(
        "LIVEKIT_URL=wss://example.test\n"
        "LIVEKIT_API_KEY=private-value\n"
        "export DEEPGRAM_API_KEY=other-private-value\n"
        "ALK_HARNESS=claude\n"
        "# COMMENTED=ignored\n",
        encoding="utf-8",
    )

    assert _declared_connection_env_names(tmp_path) == [
        "DEEPGRAM_API_KEY",
        "LIVEKIT_API_KEY",
        "LIVEKIT_URL",
    ]


def test_livekit_transport_passes_sdk_owned_credential_names(monkeypatch):
    monkeypatch.delenv("ALK_RUNTIME_CONFIGURATION_NAMES", raising=False)
    contract = SimpleNamespace(
        data_store=None,
        dependencies=[],
        runtime_dependencies=[
            SimpleNamespace(
                engine="livekit",
                reached=SimpleNamespace(
                    dsn_env="LIVEKIT_URL",
                    config_key="",
                    user="",
                    password_from="",
                ),
            )
        ],
    )

    assert _contract_runtime_configuration_names(contract) == [
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_URL",
    ]


def test_vertex_runtime_passes_project_and_credential_names(monkeypatch):
    monkeypatch.delenv("ALK_RUNTIME_CONFIGURATION_NAMES", raising=False)
    contract = SimpleNamespace(
        data_store=None,
        dependencies=[],
        runtime_dependencies=[
            SimpleNamespace(
                engine="google",
                reached=SimpleNamespace(
                    dsn_env="GOOGLE_APPLICATION_CREDENTIALS",
                    config_key="",
                    user="",
                    password_from="",
                ),
            )
        ],
    )

    assert _contract_runtime_configuration_names(contract) == [
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_CLOUD_PROJECT",
    ]
