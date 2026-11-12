from pathlib import Path

import pytest

from fi.alk.harness.credentials import (
    RequirementKind,
    RequirementStatus,
    discover_credentials,
)
from fi.alk.harness.secrets import (
    CONTROLLER_CONNECTION_NAMES,
    client_name_reserved,
    controller_name_reserved,
    controller_owned_name,
    worker_environment,
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


# TH-9550: which names a job may put in the sandbox controller's environment.


@pytest.mark.parametrize(
    "name",
    [
        # One case per prefix.
        "ALK_SANDBOX_TOKEN",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONHOME",
        "PYTHONWARNINGS",
        "HARNESS_VOICE_RUNNER",
        "HARNESS_PLATFORM_WORKSPACE_ID",
        "FI_DEFAULT_MODEL",
        "AGENTCC_CLAUDE_MODEL_ALIAS",
        "DOCKER_CONFIG",
        "COMPOSE_FILE",
        "BUILDKIT_HOST",
        "BUILDX_CONFIG",
        "NODE_OPTIONS",
        "SIMULATOR_X",
        "ANTHROPIC_VERTEX_BASE_URL",
        "GOOGLE_API_KEY",
        "VERTEXAI_PROJECT",
        "GIT_SSH_COMMAND",
        "CLAUDE_CODE_ANYTHING",
        # One case per exact name.
        "LOCALDOMAIN",
        "RES_OPTIONS",
        "HOSTALIASES",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NO_PROXY",
        "GCONV_PATH",
        "BASH_ENV",
        "ENV",
        "SSLKEYLOGFILE",
        "OPENSSL_CONF",
        # Anything ending _proxy, in any case.
        "HTTPS_PROXY",
        "ALL_PROXY",
        "wss_proxy",
        "No_Proxy",
        "http_proxy",
        # Case and surrounding spaces do not bypass it.
        "pythonpath",
        " PYTHONPATH ",
        "\tld_preload\n",
        # The runner's own names, and a name only the worker allow-list holds.
        "HARNESS_PLATFORM_URL",
        "DOCKER_HOST",
        "TMPDIR",
        "CARTESIA_API_KEY",
    ],
)
def test_controller_name_reserved(name):
    assert controller_name_reserved(name)


@pytest.mark.parametrize(
    "name", ["LK_URL", "MY_SETTING", "OPENAI_API_KEY", "REMOTE_AGENT_ID", "VAPI_API_BASE_URL"]
)
def test_controller_name_not_reserved(name):
    assert not controller_name_reserved(name)


def test_no_controller_connection_name_is_reserved_or_inherited_from_the_host():
    assert len(CONTROLLER_CONNECTION_NAMES) == 17
    assert not [n for n in CONTROLLER_CONNECTION_NAMES if controller_name_reserved(n)]
    child = worker_environment(
        {},
        host_environment={name: "host-value" for name in CONTROLLER_CONNECTION_NAMES},
    )
    assert not set(child) & CONTROLLER_CONNECTION_NAMES


def test_worker_environment_never_lets_a_job_replace_or_add_a_host_name():
    child = worker_environment(
        {
            "PYTHONPATH": "/job",
            "HARNESS_PLATFORM_URL": "https://job.invalid",
            "LD_PRELOAD": "/job.so",
            "http_proxy": "http://job.invalid",
            "NO_PROXY": "job.invalid",
            "HOME": "/job-home",
            "LIVEKIT_URL": "wss://job.invalid",
        },
        host_environment={
            "PATH": "/bin",
            "PYTHONPATH": "/host",
            "HARNESS_PLATFORM_URL": "https://platform.invalid",
            "NO_PROXY": "host.invalid",
            "HOME": "/host-home",
        },
    )

    assert child["PYTHONPATH"] == "/host"
    assert child["HARNESS_PLATFORM_URL"] == "https://platform.invalid"
    assert child["NO_PROXY"] == "host.invalid"
    assert child["HOME"] == "/host-home"
    assert "LD_PRELOAD" not in child
    assert "http_proxy" not in child
    assert child["LIVEKIT_URL"] == "wss://job.invalid"


@pytest.mark.parametrize(
    "name",
    [
        "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "DOCKER_CONFIG", "COMPOSE_FILE",
        "BUILDKIT_HOST", "BUILDX_CONFIG", "GIT_SSH_COMMAND", "CLAUDE_CODE_X",
        "SSH_ASKPASS", "XDG_CONFIG_HOME", " ssh_askpass ", "xdg_config_home",
        "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "DOCKER_HOST", "SSL_CERT_FILE",
        "SSL_CERT_DIR", "NO_PROXY", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "LOCALDOMAIN", "RES_OPTIONS", "HOSTALIASES", "GCONV_PATH", "BASH_ENV", "ENV",
        "OPENSSL_CONF", "SSLKEYLOGFILE", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
        "PYTHONUSERBASE", "PYTHONBREAKPOINT", "PYTHONPLATLIBDIR", "NODE_OPTIONS",
        "NODE_PATH", "NODE_REPL_EXTERNAL_MODULE", "NODE_EXTRA_CA_CERTS",
        "NODE_TLS_REJECT_UNAUTHORIZED", "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB",
        "http_proxy", "HTTPS_PROXY", "wss_proxy", "No_Proxy",
        # Case and whitespace variants.
        " ld_preload ", "\tnode_options\n", "pythonpath", " Http_Proxy ", "bash_env",
    ],
)
def test_client_name_reserved(name):
    assert client_name_reserved(name)
    assert controller_name_reserved(name)


@pytest.mark.parametrize(
    "name",
    [
        "HARNESS_VOICE_RUNNER", "ALK_SANDBOX_TOKEN", "AGENTCC_API_KEY", "FI_API_KEY",
        "SIMULATOR_X", "ANTHROPIC_VERTEX_BASE_URL", "GOOGLE_API_KEY", "VERTEXAI_PROJECT",
        "PYTHONUNBUFFERED", "NODE_ENV", "DOCKER_HOST", "HARNESS_PLATFORM_URL",
        "CARTESIA_API_KEY", "GOOGLE_CLOUD_PROJECT", " harness_voice_runner ", "alk_x",
    ],
)
def test_controller_owned_name(name):
    assert controller_owned_name(name)
    assert controller_name_reserved(name)


@pytest.mark.parametrize("name", ["NODE_ENV", "PYTHONUNBUFFERED", " node_env ", "MY_SETTING", "LK_URL"])
def test_client_name_reserved_passes_ordinary_runtime_settings(name):
    assert not client_name_reserved(name)


@pytest.mark.parametrize("name", ["LD_PRELOAD", "http_proxy", "GIT_SSH_COMMAND", "MY_SETTING", "LK_URL", "DEEPGRAM_API_KEY"])
def test_controller_owned_name_leaves_client_and_neutral_names(name):
    assert not controller_owned_name(name)
