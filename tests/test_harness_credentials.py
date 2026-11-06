import os
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


def test_generic_discovery_defers_template_only_secrets_but_keeps_strict_reads(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.example").write_text(
        "OPENAI_API_KEY=\nTAVILY_API_KEY=\n", encoding="utf-8"
    )
    (tmp_path / "agent.py").write_text(
        "import os\nTOKEN = os.environ['REQUIRED_RUNTIME_TOKEN']\n", encoding="utf-8"
    )

    requirements = _requirements(
        discover_credentials(tmp_path, template_secrets_required=False)
    )

    assert requirements["OPENAI_API_KEY"].required is False
    assert requirements["TAVILY_API_KEY"].required is False
    assert requirements["REQUIRED_RUNTIME_TOKEN"].required is True


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

    missing_manifest = discover_credentials(tmp_path)
    missing = _requirements(missing_manifest)
    configured = discover_credentials(
        tmp_path,
        provided_environment=["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"],
    )

    assert set(missing) == {"LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}
    assert missing["LIVEKIT_URL"].kind is RequirementKind.CONFIGURATION
    assert missing["LIVEKIT_API_KEY"].kind is RequirementKind.SECRET
    assert missing["LIVEKIT_API_SECRET"].kind is RequirementKind.SECRET
    assert configured.ready


def test_livekit_provider_sdk_credentials_are_discovered_from_constructor_use(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "agent.py",
        """
from livekit.plugins import deepgram, google
stt = deepgram.STT(model="nova-3")
llm = google.LLM(model="gemini-2.5-flash", vertexai=True,
                 project=os.environ["GOOGLE_CLOUD_PROJECT"])
""",
    )

    missing_manifest = discover_credentials(tmp_path)
    missing = _requirements(missing_manifest)
    configured = discover_credentials(
        tmp_path,
        provided_environment=[
            "LIVEKIT_URL",
            "LIVEKIT_API_KEY",
            "LIVEKIT_API_SECRET",
            "DEEPGRAM_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS_JSON",
            "GOOGLE_CLOUD_PROJECT",
        ],
    )

    assert missing["DEEPGRAM_API_KEY"].status is RequirementStatus.MISSING
    assert (
        missing["GOOGLE_APPLICATION_CREDENTIALS_JSON"].status
        is RequirementStatus.MISSING
    )
    assert missing["GOOGLE_CLOUD_PROJECT"].status is RequirementStatus.MISSING
    assert missing_manifest.credential_choices[0].options == [
        ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"],
        ["GOOGLE_APPLICATION_CREDENTIALS_JSON", "GOOGLE_CLOUD_PROJECT"],
    ]
    assert configured.ready


def test_uploaded_google_json_satisfies_conventional_adc_path(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent.py",
        'credentials = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]\n',
    )

    manifest = discover_credentials(
        tmp_path, provided_environment=["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
    )

    assert manifest.ready
    assert (
        _requirements(manifest)["GOOGLE_APPLICATION_CREDENTIALS"].status
        is RequirementStatus.CONFIGURED
    )


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


def test_plain_compose_substitution_is_optional_but_error_operator_is_required(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "compose.yml",
        """services:
  agent:
    image: example/agent
    environment:
      OPTIONAL_REGION: ${OPTIONAL_REGION}
      REQUIRED_TOKEN: ${REQUIRED_TOKEN:?supply token}
""",
    )

    requirements = _requirements(discover_credentials(tmp_path))

    assert requirements["OPTIONAL_REGION"].status is RequirementStatus.OPTIONAL
    assert requirements["REQUIRED_TOKEN"].status is RequirementStatus.MISSING


def test_compose_literal_environment_satisfies_runtime_read(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "compose.yml",
        """services:
  agent:
    build: .
    environment:
      CACHE_URL: redis://cache:6379/2
      REQUIRED_TOKEN: ${REQUIRED_TOKEN:?required}
""",
    )
    _write(tmp_path, "agent.py", "import os\nurl = os.environ['CACHE_URL']\n")

    requirements = _requirements(discover_credentials(tmp_path))

    assert requirements["CACHE_URL"].status is RequirementStatus.CONFIGURED
    assert requirements["REQUIRED_TOKEN"].status is RequirementStatus.MISSING


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


@pytest.mark.parametrize("filename", ["env.example", "env.sample", "env.template"])
def test_common_non_dot_env_templates_are_discovered(
    tmp_path: Path, filename: str
) -> None:
    _write(tmp_path, filename, "GOOGLE_API_KEY=[project]\nSAFE_MODE=true\n")

    requirements = _requirements(discover_credentials(tmp_path))

    assert requirements["GOOGLE_API_KEY"].status is RequirementStatus.MISSING
    assert requirements["GOOGLE_API_KEY"].kind is RequirementKind.SECRET
    assert requirements["SAFE_MODE"].status is RequirementStatus.OPTIONAL


@pytest.mark.parametrize(
    "placeholder", ["<api-key>", "{TOKEN}", "your-api-key", "replace_me", "CHANGEME"]
)
def test_template_placeholders_are_not_treated_as_working_defaults(
    tmp_path: Path, placeholder: str
) -> None:
    _write(tmp_path, ".env.example", f"OPENAI_API_KEY={placeholder}\n")

    requirement = _requirements(discover_credentials(tmp_path))["OPENAI_API_KEY"]

    assert requirement.required
    assert requirement.status is RequirementStatus.MISSING


def test_compose_interpolation_inside_env_value_is_not_a_compose_requirement(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "env.example",
        "PUBLIC_URL=https://voice.example/${AGENT_NAME}\n",
    )

    requirements = _requirements(discover_credentials(tmp_path))

    assert "AGENT_NAME" not in requirements
    assert requirements["PUBLIC_URL"].status is RequirementStatus.OPTIONAL


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


def test_environment_writes_and_quoted_reads_are_not_requirements(tmp_path: Path) -> None:
    _write(tmp_path, "config.py", '''
import os
os.environ["AUTH_JWT_SECRET"] = generated_secret
os.environ["DEERFLOW_REPLAY_FIXTURE"] = str(fixture)
os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(config_path)
# os.environ["COMMENT_API_KEY"]
example = 'os.environ["EXAMPLE_API_KEY"]'
token = os.environ["REQUIRED_RUNTIME_TOKEN"]
guarded_in_comment = os.environ["COMMENT_GUARD_TOKEN"]  # os.getenv("COMMENT_GUARD_TOKEN")
optional = os.getenv("OPTIONAL_API_KEY")
''')

    requirements = _requirements(discover_credentials(tmp_path))

    assert set(requirements) == {
        "REQUIRED_RUNTIME_TOKEN", "COMMENT_GUARD_TOKEN", "OPTIONAL_API_KEY"
    }
    assert requirements["REQUIRED_RUNTIME_TOKEN"].required
    assert requirements["COMMENT_GUARD_TOKEN"].required
    assert not requirements["OPTIONAL_API_KEY"].required


def test_environment_assignment_value_still_requires_its_read(tmp_path: Path) -> None:
    _write(tmp_path, "config.py", '''
os.environ["DESTINATION_TOKEN"] = os.environ["SOURCE_TOKEN"]
os.environ["APPENDED_TOKEN"] += "suffix"
''')

    requirements = _requirements(discover_credentials(tmp_path))

    assert set(requirements) == {"SOURCE_TOKEN", "APPENDED_TOKEN"}
    assert all(item.required for item in requirements.values())


def test_unparseable_python_keeps_conservative_indexed_requirements(tmp_path: Path) -> None:
    _write(tmp_path, "agent.py", 'token = os.environ["REQUIRED_TOKEN"]\nif (\n')

    requirement = _requirements(discover_credentials(tmp_path))["REQUIRED_TOKEN"]

    assert requirement.required


def test_blank_template_configuration_is_not_a_runtime_requirement(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        ".env.example",
        "TEMPERATURE=\nPROMPT_LABEL=\nOPENAI_API_KEY=\n",
    )

    requirements = _requirements(discover_credentials(tmp_path))

    assert requirements["TEMPERATURE"].status is RequirementStatus.OPTIONAL
    assert requirements["PROMPT_LABEL"].status is RequirementStatus.OPTIONAL
    assert requirements["OPENAI_API_KEY"].status is RequirementStatus.MISSING


def test_dependency_environment_directories_are_not_scanned(tmp_path: Path) -> None:
    _write(tmp_path, "agent.py", 'TOKEN = "not a credential"\n')
    for directory in ("venv", ".tox", ".nox"):
        _write(
            tmp_path,
            f"{directory}/lib/python/site-packages/vendor.py",
            'import os\nkey = os.environ["VENDOR_INTERNAL_API_KEY"]\n',
        )

    manifest = discover_credentials(tmp_path)

    assert "VENDOR_INTERNAL_API_KEY" not in _requirements(manifest)


def test_alk_runtime_namespace_is_platform_owned(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "provider.py",
        """
import os
context = os.environ["ALK_PROVIDER_CONTEXT"]
events = os.environ["ALK_EVENT_URL"]
tools = os.environ["ALK_TOOL_BASE_URL"]
output = os.environ["ALK_PROVIDER_OUTPUT"]
receipt = os.environ["ALK_PROVIDER_RECEIPT"]
""",
    )

    manifest = discover_credentials(tmp_path)

    assert manifest.ready
    assert not manifest.requirements


def test_explicit_llm_provider_makes_model_credentials_alternatives(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "agent.py",
        """
import os
provider = os.getenv("LLM_PROVIDER") or "vertex"
project = os.getenv("GOOGLE_CLOUD_PROJECT")
credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if provider == "agentcc":
    key = os.environ["AGENTCC_API_KEY"]
""",
    )

    manifest = discover_credentials(
        tmp_path,
        provided_environment=[
            "LLM_PROVIDER",
            "GOOGLE_APPLICATION_CREDENTIALS_JSON",
            "GOOGLE_CLOUD_PROJECT",
        ],
    )

    assert manifest.ready
    assert manifest.credential_choices[0].options == [
        ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"],
        ["AGENTCC_API_KEY"],
    ]


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


def test_langchain_vertex_constructor_discovers_adc_consumed_by_sdk(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "agent.py",
        """
import os
from langchain_google_genai import ChatGoogleGenerativeAI

provider = os.getenv("LLM_PROVIDER") or "vertex"
project = os.getenv("GOOGLE_CLOUD_PROJECT")
model = ChatGoogleGenerativeAI(
    model=os.getenv("MODEL_NAME") or "gemini-2.5-flash",
    project=project,
    vertexai=True,
)
if provider == "openai_compatible":
    key = os.getenv("AGENTCC_API_KEY") or os.getenv("OPENAI_API_KEY")
""",
    )

    missing = discover_credentials(tmp_path)
    configured = discover_credentials(
        tmp_path,
        provided_environment=[
            "LLM_PROVIDER",
            "GOOGLE_APPLICATION_CREDENTIALS_JSON",
            "GOOGLE_CLOUD_PROJECT",
        ],
    )

    assert not missing.ready
    assert configured.ready
    assert any(
        requirement.environment_name == "GOOGLE_APPLICATION_CREDENTIALS_JSON"
        and "sdk:langchain_google_genai.ChatGoogleGenerativeAI.vertex"
        in requirement.detected_from
        for requirement in configured.requirements
    )


def test_non_google_model_alternatives_do_not_form_google_auth_choice(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "agent.py",
        """
import os
provider = os.getenv("LLM_PROVIDER")
openai_key = os.getenv("OPENAI_API_KEY")
agentcc_key = os.getenv("AGENTCC_API_KEY")
""",
    )

    manifest = discover_credentials(tmp_path)

    assert manifest.credential_choices == []


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


def test_explicit_scan_paths_exclude_unused_optional_integrations(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "compose.yml",
        "services:\n  api:\n    image: example/api\n"
        "    environment:\n      OPENAI_API_KEY: ${OPENAI_API_KEY:?required}\n",
    )
    _write(
        tmp_path,
        "optional/retell.py",
        "import os\nimport retell\nkey = os.environ['RETELL_API_KEY']\n",
    )

    manifest = discover_credentials(tmp_path, scan_paths=["compose.yml"])

    assert set(_requirements(manifest)) == {"OPENAI_API_KEY"}
    assert "retell" not in manifest.detected_connectors


def _agents_repo(root, files):
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body, encoding="utf-8")
    return root


def _required(manifest):
    return {item.environment_name: item.required for item in manifest.requirements}


def test_shared_code_the_folder_imports_requires_its_values(tmp_path):
    root = _agents_repo(
        tmp_path,
        {
            "common/__init__.py": "",
            "common/llm.py": 'import os\nKEY = os.environ["SHARED_API_KEY"]\n',
            "agents/a/agent.py": "from common import llm\n",
            "agents/b/agent.py": 'import os\nKEY = os.environ["B_ONLY_API_KEY"]\n',
        },
    )

    required = _required(discover_credentials(root, agent_subfolder="agents/a"))

    assert required["SHARED_API_KEY"] is True
    assert required["B_ONLY_API_KEY"] is False


def test_imports_are_followed_past_the_first_file(tmp_path):
    root = _agents_repo(
        tmp_path,
        {
            "common/first.py": "from . import second\n",
            "common/second.py": 'import os\nKEY = os.environ["DEEP_API_KEY"]\n',
            "agents/a/agent.py": "import common.first\n",
        },
    )

    assert _required(discover_credentials(root, agent_subfolder="agents/a"))["DEEP_API_KEY"] is True


def test_a_siblings_connector_requires_nothing(tmp_path):
    root = _agents_repo(
        tmp_path, {"agents/a/agent.py": "x = 1\n", "agents/b/agent.py": "import livekit\n"}
    )
    with_folder = discover_credentials(root, agent_subfolder="agents/a")
    without = discover_credentials(root)

    assert with_folder.detected_connectors == []
    assert without.detected_connectors != []


def test_no_folder_keeps_every_hit_required(tmp_path):
    root = _agents_repo(
        tmp_path, {"agents/b/agent.py": 'import os\nKEY = os.environ["B_ONLY_API_KEY"]\n'}
    )

    assert _required(discover_credentials(root))["B_ONLY_API_KEY"] is True


@pytest.mark.parametrize(
    "line",
    [
        'import { llm } from "../../packages/shared/llm";',
        'const llm = require("../../packages/shared/llm.ts");',
        'const llm = await import("../../packages/shared/llm");',
        'import { llm } from "packages/shared/llm";',
    ],
)
def test_shared_scripts_the_folder_imports_require_their_values(tmp_path, line):
    root = _agents_repo(
        tmp_path,
        {
            "packages/shared/llm.ts": "export const key = process.env.SHARED_API_KEY;\n",
            "packages/other/x.ts": "export const key = process.env.OTHER_API_KEY;\n",
            "agents/a/index.ts": f"{line}\n",
        },
    )

    required = _required(discover_credentials(root, agent_subfolder="agents/a"))

    assert required["SHARED_API_KEY"] is True
    assert required["OTHER_API_KEY"] is False


def test_a_script_import_reaches_an_index_and_a_compiled_name(tmp_path):
    root = _agents_repo(
        tmp_path,
        {
            "lib/index.ts": 'export * from "./real.js";\n',
            "lib/real.ts": "export const key = process.env.REAL_API_KEY;\n",
            "agents/a/index.js": 'import "../../lib";\n',
        },
    )

    assert _required(discover_credentials(root, agent_subfolder="agents/a"))["REAL_API_KEY"] is True


@pytest.mark.parametrize("make", ["link", "fifo"])
def test_the_import_walk_does_not_read_a_link_or_a_pipe(tmp_path, make):
    from fi.alk.harness.credentials import imported_by_agent

    root = _agents_repo(tmp_path, {"agents/a/agent.py": "x = 1\n"})
    target = root / "agents" / "a" / "x.py"
    if make == "link":
        target.symlink_to("/dev/zero")
    else:
        os.mkfifo(target)

    assert imported_by_agent(root, "agents/a") == frozenset()


def test_the_import_walk_does_not_follow_a_link_out_or_read_an_oversized_file(tmp_path):
    from fi.alk.harness import credentials

    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    root = _agents_repo(
        tmp_path / "repo",
        {
            "common/big.py": "#" * (credentials._MAX_FILE_BYTES + 1),
            "agents/a/agent.py": "import common.big\nimport linked\n",
        },
    )
    (root / "linked.py").symlink_to(outside)

    assert credentials.imported_by_agent(root, "agents/a") == frozenset()


def test_the_import_walk_stops_at_the_file_limit(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    root = _agents_repo(
        tmp_path,
        {
            "common/one.py": "import common.two\n",
            "common/two.py": "x = 1\n",
            "agents/a/agent.py": "import common.one\n",
        },
    )
    monkeypatch.setattr(credentials, "_MAX_FILES", 1)

    assert credentials.imported_by_agent(root, "agents/a") == frozenset({(root / "common/one.py").resolve()})


_STRICT_COMPOSE = "services:\n  billing:\n    environment:\n      STRIPE_SECRET_KEY: ${STRIPE_SECRET_KEY:?}\n"


def test_a_root_compose_file_requires_nothing_unless_it_is_named(tmp_path):
    root = _agents_repo(
        tmp_path, {"docker-compose.yml": _STRICT_COMPOSE, "agents/a/agent.py": "x = 1\n"}
    )

    unnamed = discover_credentials(root, agent_subfolder="agents/a")
    named = discover_credentials(
        root, agent_subfolder="agents/a", agent_compose_file="docker-compose.yml"
    )

    assert _required(unnamed)["STRIPE_SECRET_KEY"] is False
    assert _required(named)["STRIPE_SECRET_KEY"] is True


def test_a_compose_file_in_the_folder_is_the_agents(tmp_path):
    root = _agents_repo(tmp_path, {"agents/a/docker-compose.yml": _STRICT_COMPOSE})

    assert _required(discover_credentials(root, agent_subfolder="agents/a"))["STRIPE_SECRET_KEY"] is True


@pytest.mark.parametrize(
    "line",
    [
        "import" + " " * 400_000 + "y",
        "import(" + " " * 400_000 + "y",
        "require(" + " " * 400_000 + "y",
        "from" + " " * 400_000 + "y",
        "import type" + " " * 400_000 + "y",
        "import " * 100_000,
        "import type a " * 40_000,
    ],
    ids=["import", "import-call", "require", "from", "type", "repeated", "repeated-type"],
)
def test_an_odd_script_keeps_the_import_walk_quick(tmp_path, line):
    import time

    from fi.alk.harness.credentials import imported_by_agent

    root = _agents_repo(tmp_path, {"agents/a/x.js": line})
    started = time.monotonic()

    imported_by_agent(root, "agents/a")

    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("routes.js", 'const note = from "/";\n'),
        ("routes.ts", 'const url = from "";\nconst other = require("\\0");\n'),
        ("deep.py", "from " + "." * 40 + " import x\n"),
        ("deep2.py", "from " + "." * 40 + "pkg import x\n"),
    ],
)
def test_one_odd_import_does_not_stop_the_walk(tmp_path, name, body):
    from fi.alk.harness.credentials import imported_by_agent

    root = _agents_repo(
        tmp_path,
        {
            "common/llm.py": "x = 1\n",
            f"agents/a/{name}": body,
            "agents/a/agent.py": "import common.llm\n",
        },
    )

    assert (root / "common/llm.py").resolve() in imported_by_agent(root, "agents/a")


def test_an_import_runs_the_packages_above_it(tmp_path):
    root = _agents_repo(
        tmp_path,
        {
            "pkg/__init__.py": "from . import config\n",
            "pkg/config.py": 'import os\nKEY = os.environ["PACKAGE_API_KEY"]\n',
            "pkg/mod.py": "x = 1\n",
            "agents/a/agent.py": "from pkg.mod import x\n",
        },
    )

    assert _required(discover_credentials(root, agent_subfolder="agents/a"))["PACKAGE_API_KEY"] is True


def test_a_siblings_default_does_not_clear_the_agents_requirement(tmp_path):
    root = _agents_repo(
        tmp_path,
        {
            "agents/a/agent.py": 'import os\nNAME = os.environ["MODEL_NAME"]\n',
            "agents/b/.env.example": "MODEL_NAME=gpt-4o\n",
        },
    )

    assert _required(discover_credentials(root, agent_subfolder="agents/a"))["MODEL_NAME"] is True


@pytest.mark.parametrize(
    "line",
    [
        '// import { key } from "../b/llm";',
        '/* const key = require("../b/llm"); */',
        'import type { Key } from "../b/llm";',
    ],
)
def test_a_comment_or_a_type_import_is_not_followed(tmp_path, line):
    root = _agents_repo(
        tmp_path,
        {
            "agents/a/ui.ts": f"{line}\n",
            "agents/b/llm.ts": "export const key = process.env.BILLING_API_KEY;\n",
        },
    )

    assert _required(discover_credentials(root, agent_subfolder="agents/a"))["BILLING_API_KEY"] is False


def test_a_byte_order_mark_does_not_hide_a_files_imports(tmp_path):
    root = _agents_repo(
        tmp_path,
        {
            "common/llm.py": 'import os\nKEY = os.environ["SHARED_API_KEY"]\n',
            "agents/a/agent.py": "\ufeffimport common.llm\n",
        },
    )

    assert _required(discover_credentials(root, agent_subfolder="agents/a"))["SHARED_API_KEY"] is True


def test_the_file_limit_reads_the_agents_files_first(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    root = _agents_repo(
        tmp_path,
        {
            "aaa/one.py": "x = 1\n",
            "aaa/two.py": "x = 1\n",
            "agents/a/agent.py": 'import os\nKEY = os.environ["A_ONLY_API_KEY"]\n',
        },
    )
    monkeypatch.setattr(credentials, "_MAX_FILES", 1)

    assert _required(discover_credentials(root, agent_subfolder="agents/a"))["A_ONLY_API_KEY"] is True


def test_the_same_import_many_times_counts_once_against_the_budget(tmp_path):
    from fi.alk.harness.credentials import _MAX_WALK_IMPORTS, imported_by_agent

    body = 'import"./gone";' * (_MAX_WALK_IMPORTS + 1) + 'import"../../common/real";'
    root = _agents_repo(
        tmp_path,
        {
            "common/real.js": 'import "./deep";\n',
            "common/deep.js": "export const x = 1;\n",
            "agents/a/x.js": body,
        },
    )

    assert (root / "common/deep.js").resolve() in imported_by_agent(root, "agents/a")


def test_the_import_walk_stops_at_its_byte_budget(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    root = _agents_repo(
        tmp_path,
        {
            "common/one.py": "import common.two\n",
            "common/two.py": "x = 1\n",
            "agents/a/agent.py": "import common.one\n",
        },
    )
    monkeypatch.setattr(credentials, "_MAX_WALK_BYTES", 1)

    assert credentials.imported_by_agent(root, "agents/a") == frozenset(
        {(root / "common/one.py").resolve()}
    )


def test_the_import_walk_stops_at_its_import_budget(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    root = _agents_repo(
        tmp_path,
        {
            "common/one.py": "import common.two\n",
            "common/two.py": "x = 1\n",
            "agents/a/agent.py": "import common.one\n",
        },
    )
    monkeypatch.setattr(credentials, "_MAX_WALK_IMPORTS", 1)

    assert credentials.imported_by_agent(root, "agents/a") == frozenset(
        {(root / "common/one.py").resolve()}
    )


def test_the_walk_reads_the_folders_own_files_before_a_chain(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    root = _agents_repo(
        tmp_path,
        {
            "agents/a/agent.py": "import common.web\n",
            "agents/a/zz.py": "import chain.one\n",
            "chain/one.py": "import chain.two\n",
            "chain/two.py": "import chain.three\n",
            "chain/three.py": "x = 1\n",
            "common/web.py": "import common.deep\n",
            "common/deep.py": "x = 1\n",
        },
    )
    monkeypatch.setattr(credentials, "_MAX_WALK_IMPORTS", 3)

    assert (root / "common/deep.py").resolve() in credentials.imported_by_agent(root, "agents/a")


def test_the_walk_prefers_the_folders_copy_of_a_module(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    root = _agents_repo(
        tmp_path,
        {
            "agents/a/agent.py": "import shared\n",
            "agents/a/shared.py": "x = 1\n",
            "shared.py": "x = 1\n",
        },
    )

    found = imported_by_agent(root, "agents/a")

    assert (root / "agents/a/shared.py").resolve() in found
    assert (root / "shared.py").resolve() not in found


def test_an_odd_script_import_does_not_stop_the_files_other_imports(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    root = _agents_repo(
        tmp_path,
        {
            "common/util.js": "export const x = 1;\n",
            # "/" can't name a module, and makes the path code raise.
            "agents/a/routes.js": 'import a from "/";\nimport b from "../../common/util.js";\n',
        },
    )

    assert (root / "common/util.js").resolve() in imported_by_agent(root, "agents/a")
