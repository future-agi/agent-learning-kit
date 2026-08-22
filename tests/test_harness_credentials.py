from pathlib import Path

import pytest

from fi.alk.harness.credentials import (
    RequirementKind,
    RequirementStatus,
    discover_credentials,
)
from fi.simulate.runtime.spec import SecretRef


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _requirements(manifest):
    return {item.environment_name: item for item in manifest.requirements}


def test_discovers_python_livekit_agent_without_executing_it(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent.py",
        """
import os
from livekit import agents
LIVEKIT_KEY = os.environ["LIVEKIT_API_KEY"]
DEEPGRAM_KEY = os.getenv("DEEPGRAM_API_KEY")
PORT = os.getenv("PORT", "8080")
""",
    )
    _write(
        tmp_path,
        ".env.example",
        "LIVEKIT_API_SECRET=\nDATABASE_URL=postgres://generated\n",
    )

    manifest = discover_credentials(tmp_path)
    requirements = _requirements(manifest)

    assert "livekit" in manifest.detected_connectors
    assert requirements["LIVEKIT_URL"].status is RequirementStatus.MISSING
    assert requirements["LIVEKIT_API_KEY"].status is RequirementStatus.MISSING
    assert requirements["LIVEKIT_API_SECRET"].kind is RequirementKind.SECRET
    assert requirements["DEEPGRAM_API_KEY"].provider == "deepgram"
    assert requirements["DATABASE_URL"].status is RequirementStatus.HARNESS_PROVIDED
    assert "PORT" not in requirements


def test_livekit_sdk_implicit_credentials_are_discovered_without_getenv_calls(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "agent.py",
        "from livekit.agents import AgentServer, cli\ncli.run_app(AgentServer())\n",
    )

    missing = _requirements(discover_credentials(tmp_path))
    configured = discover_credentials(
        tmp_path,
        provided_environment=["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"],
    )

    assert set(missing) == {"LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}
    assert missing["LIVEKIT_URL"].kind is RequirementKind.CONFIGURATION
    assert missing["LIVEKIT_API_KEY"].kind is RequirementKind.SECRET
    assert missing["LIVEKIT_API_SECRET"].kind is RequirementKind.SECRET
    assert configured.ready


def test_discovers_typescript_vapi_agent_and_marks_secret_reference_configured(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "src/index.ts",
        """
import Vapi from '@vapi-ai/server-sdk';
const key = process.env.VAPI_API_KEY;
const assistant = process.env.VAPI_ASSISTANT_ID;
""",
    )
    manifest = discover_credentials(
        tmp_path,
        secret_refs={
            "VAPI_API_KEY": SecretRef(
                manager="futureagi",
                key="customer-vapi-key",
                purpose="hosted voice agent connection",
            )
        },
    )
    requirements = _requirements(manifest)

    assert "vapi" in manifest.detected_connectors
    assert requirements["VAPI_API_KEY"].status is RequirementStatus.CONFIGURED
    assert requirements["VAPI_ASSISTANT_ID"].kind is RequirementKind.CONFIGURATION
    assert requirements["VAPI_ASSISTANT_ID"].status is RequirementStatus.MISSING


def test_discovers_twilio_compose_agent_and_respects_optional_defaults(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "docker-compose.yml",
        """
services:
  agent:
    image: example/twilio-agent
    environment:
      TWILIO_ACCOUNT_SID: ${TWILIO_ACCOUNT_SID:?required}
      TWILIO_AUTH_TOKEN: ${TWILIO_AUTH_TOKEN:?required}
      TWILIO_REGION: ${TWILIO_REGION:-us1}
      REDIS_URL: ${REDIS_URL:-redis://redis:6379}
""",
    )

    requirements = _requirements(discover_credentials(tmp_path))

    assert requirements["TWILIO_ACCOUNT_SID"].status is RequirementStatus.MISSING
    assert requirements["TWILIO_AUTH_TOKEN"].kind is RequirementKind.SECRET
    assert requirements["TWILIO_REGION"].status is RequirementStatus.OPTIONAL
    assert requirements["REDIS_URL"].status is RequirementStatus.HARNESS_PROVIDED


def test_discovery_ignores_dependencies_symlinks_and_large_files(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "node_modules/sdk/index.js", "process.env.STOLEN_TOKEN")
    _write(tmp_path, "large.py", "x" * 1_000_001 + "os.environ['LARGE_SECRET']")
    external = tmp_path / "external.txt"
    external.write_text("import os; os.environ['OUTSIDE_SECRET']", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to(external)
    _write(tmp_path, "app.py", "import os; os.environ['REAL_API_KEY']")

    names = set(_requirements(discover_credentials(tmp_path)))

    assert names == {"REAL_API_KEY"}


def test_manifest_is_deterministic_and_contains_no_values(tmp_path: Path) -> None:
    _write(tmp_path, ".env.example", "OPENAI_API_KEY=\nMODEL_NAME=gpt-test\n")

    first = discover_credentials(tmp_path)
    second = discover_credentials(tmp_path)

    assert first == second
    encoded = first.model_dump_json()
    assert "gpt-test" not in encoded
    assert "OPENAI_API_KEY" in encoded


def test_source_constants_tests_and_declared_defaults_are_not_credentials(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "agent.py",
        'INSTRUCTIONS = "be helpful"\nimport os\nTIMEOUT = os.environ["TIMEOUT"]\n',
    )
    _write(tmp_path, ".env.example", "TIMEOUT=5\n")
    _write(tmp_path, "tests/test_agent.py", "import os\nos.environ['TEST_TOKEN']\n")

    requirements = _requirements(discover_credentials(tmp_path))

    assert set(requirements) == {"TIMEOUT"}
    assert requirements["TIMEOUT"].status is RequirementStatus.OPTIONAL


def test_guarded_indexed_environment_access_is_optional(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "clock.py",
        'today = os.environ["HOTEL_TODAY"] if os.environ.get("HOTEL_TODAY") else date.today()\n',
    )

    requirement = _requirements(discover_credentials(tmp_path))["HOTEL_TODAY"]

    assert not requirement.required
    assert requirement.status is RequirementStatus.OPTIONAL


def test_google_model_auth_is_one_explicit_credential_choice(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "config.py",
        """
import os
api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
project = os.getenv("GOOGLE_CLOUD_PROJECT")
""",
    )

    missing = discover_credentials(tmp_path)
    configured = discover_credentials(tmp_path, provided_environment=["GEMINI_API_KEY"])

    assert not missing.ready
    assert missing.credential_choices[0].options == [
        ["GEMINI_API_KEY"],
        ["GOOGLE_API_KEY"],
        ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"],
    ]
    assert configured.ready


@pytest.mark.parametrize(
    ("filename", "source", "connector", "required_name"),
    [
        (
            "voice.py",
            "import os\nimport pipecat\nkey = os.environ['CARTESIA_API_KEY']\n",
            "pipecat",
            "CARTESIA_API_KEY",
        ),
        (
            "mcp_agent.py",
            "import os\nfrom mcp import ClientSession\ntoken = os.environ['ANTHROPIC_API_KEY']\n",
            "mcp",
            "ANTHROPIC_API_KEY",
        ),
        (
            "retell.ts",
            "import Retell from 'retell-sdk';\nconst key = process.env.RETELL_API_KEY;\n",
            "retell",
            "RETELL_API_KEY",
        ),
        (
            "server.py",
            "import os\nfrom fastapi import FastAPI\nkey = os.environ['OPENAI_API_KEY']\n",
            "http",
            "OPENAI_API_KEY",
        ),
    ],
)
def test_agent_setup_compatibility_matrix(
    tmp_path: Path,
    filename: str,
    source: str,
    connector: str,
    required_name: str,
) -> None:
    _write(tmp_path, filename, source)

    manifest = discover_credentials(tmp_path)

    assert connector in manifest.detected_connectors
    assert _requirements(manifest)[required_name].status is RequirementStatus.MISSING
