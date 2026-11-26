from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from fi.alk.harness import instrumentation_evidence, python_source, tools
from fi.alk.harness.instrumentation_evidence import detect_instrumentation
from fi.alk.harness.python_source import LARGEST_PARSED_MODULE
from fi.alk.harness.tools import accept_contract

RECORDED_CONTRACT = (
    Path(__file__).parents[1]
    / "fixtures"
    / "hosted_bundle_v2"
    / "chat_authoring"
    / "contract.json"
)


def _repository(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"agent.py": "from fi_instrumentation import register\n"}, "traceai"),
        (
            {"app/tracing.py": "from traceai_langchain import LangChainInstrumentor\n"},
            "traceai",
        ),
        (
            {
                "agent.py": (
                    "from opentelemetry.sdk.trace import TracerProvider\n"
                    "from opentelemetry.exporter.otlp.proto.http.trace_exporter "
                    "import OTLPSpanExporter\n"
                )
            },
            "opentelemetry",
        ),
        (
            {"agent.py": "from opentelemetry.sdk.trace import TracerProvider\n"},
            "opentelemetry",
        ),
        (
            {
                "agent.py": (
                    "from opentelemetry.sdk import trace\n"
                    "from opentelemetry.exporter.otlp.proto.grpc.trace_exporter "
                    "import OTLPSpanExporter\n"
                )
            },
            "none",
        ),
        (
            {
                "agent.py": (
                    "from opentelemetry.sdk.trace import TracerProvider\n"
                    "from opentelemetry.sdk.trace.export import ConsoleSpanExporter\n"
                    "from opentelemetry.exporter.otlp.proto.grpc.trace_exporter "
                    "import OTLPSpanExporter\n"
                )
            },
            "none",
        ),
        (
            {
                "agent.py": (
                    "from opentelemetry.sdk.trace import TracerProvider\n"
                    "from opentelemetry.exporter.otlp.proto.grpc.trace_exporter "
                    "import OTLPSpanExporter\n"
                    "from opentelemetry.exporter.prometheus import PrometheusMetricReader\n"
                )
            },
            "none",
        ),
        (
            {
                "agent.py": (
                    "from opentelemetry.sdk.trace import TracerProvider\n"
                    "from opentelemetry.exporter.otlp.proto.grpc.trace_exporter "
                    "import OTLPSpanExporter\n"
                    "from opentelemetry.exporter.zipkin.json import ZipkinExporter\n"
                )
            },
            "none",
        ),
        (
            {
                "agent.py": (
                    "from opentelemetry.sdk.trace import TracerProvider\n"
                    "from opentelemetry.exporter.otlp.proto.grpc.trace_exporter "
                    "import OTLPSpanExporter\n"
                ),
                "app/http_tracing.py": (
                    "from opentelemetry.exporter.otlp.proto.http.trace_exporter "
                    "import OTLPSpanExporter\n"
                ),
            },
            "opentelemetry",
        ),
        (
            {
                "agent.py": (
                    "from opentelemetry.sdk.trace import TracerProvider\n"
                    "from opentelemetry.sdk.trace.export import ConsoleSpanExporter\n"
                )
            },
            "opentelemetry",
        ),
        ({"agent.py": "from opentelemetry import trace\n"}, "none"),
        (
            {
                "requirements.txt": "opentelemetry-sdk==1.27.0\n",
                "agent.py": "import json\n",
            },
            "none",
        ),
        (
            {
                "pyproject.toml": '[project]\ndependencies = ["traceai-livekit"]\n',
                "agent.py": "import os\n",
            },
            "none",
        ),
        (
            {
                "agent.py": "import traceai_livekit\n",
                ".env.example": "FI_API_KEY=\n",
                "tests/test_agent.py": "import os\nos.environ['FI_API_KEY'] = 'x'\n",
            },
            "traceai",
        ),
        (
            {
                "package.json": '{"dependencies": {"@traceai/langchain": "1.0.0"}}\n',
                "index.js": "require('@opentelemetry/sdk-trace-node')\n",
            },
            # A Node agent is read from its package.json, not its code.
            "traceai",
        ),
        ({"agent.py": "print('hello')\n"}, "none"),
        ({".venv/lib/vendored.py": "import fi_instrumentation\n"}, "none"),
        ({"broken.py": "def (:\n", "agent.py": "import traceai_openai\n"}, "traceai"),
    ],
    ids=[
        "fi_instrumentation",
        "traceai_package",
        "otel_sdk_with_http_exporter",
        "otel_sdk_without_exporter",
        "otel_sdk_with_grpc_exporter_only",
        "otel_sdk_with_grpc_exporter_and_console_exporter",
        "otel_sdk_with_grpc_exporter_and_prometheus_exporter",
        "otel_sdk_with_grpc_exporter_and_zipkin_exporter",
        "otel_sdk_with_grpc_and_http_exporters",
        "otel_sdk_with_console_exporter_only",
        "otel_api_only",
        "frozen_requirements_only",
        "declared_dependency_only",
        "env_files_change_nothing",
        "javascript_repository",
        "neither",
        "virtualenv_ignored",
        "unparseable_file_skipped",
    ],
)
def test_detects_traceai_opentelemetry_or_none_from_imports(
    tmp_path: Path, files: dict[str, str], expected: str
) -> None:
    assert detect_instrumentation(_repository(tmp_path, files)) == expected


def test_no_source_root_is_none() -> None:
    assert detect_instrumentation(None) == "none"


def test_the_detectors_word_replaces_the_models(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source", {"agent.py": "import json\n"})
    payload = {
        **json.loads(RECORDED_CONTRACT.read_text(encoding="utf-8")),
        "instrumentation": "traceai",
    }

    result = accept_contract(payload, tmp_path / "out", source_root=source)

    saved = json.loads((tmp_path / "out" / "contract.json").read_text(encoding="utf-8"))
    assert not result.get("is_error"), result
    assert saved["instrumentation"] == "none"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            {
                "traceai_helpers.py": "def ping():\n    return 1\n",
                "agent.py": "import traceai_helpers\n",
            },
            "none",
        ),
        (
            {
                "opentelemetry/__init__.py": "",
                "opentelemetry/sdk/__init__.py": "",
                "opentelemetry/sdk/trace.py": "",
                "agent.py": "from opentelemetry.sdk.trace import TracerProvider\n",
            },
            "none",
        ),
        (
            {
                "app/opentelemetry/__init__.py": "",
                "app/opentelemetry/sdk/__init__.py": "",
                "app/opentelemetry/sdk/trace.py": "",
                "agent.py": "from opentelemetry.sdk.trace import TracerProvider\n",
            },
            "opentelemetry",
        ),
        (
            {
                "opentelemetry.py": "",
                "agent.py": "from opentelemetry.sdk.trace import TracerProvider\n",
            },
            "none",
        ),
        (
            {
                "app/tracing/opentelemetry.py": (
                    "from opentelemetry.sdk.trace import TracerProvider\n"
                    "from opentelemetry.exporter.otlp.proto.http.trace_exporter "
                    "import OTLPSpanExporter\n"
                )
            },
            "opentelemetry",
        ),
        (
            {
                "docs/opentelemetry/README.md": "How we trace.\n",
                "agent.py": "from opentelemetry.sdk.trace import TracerProvider\n",
            },
            "opentelemetry",
        ),
        (
            {
                "agent.py": (
                    "from typing import TYPE_CHECKING\n"
                    "if TYPE_CHECKING:\n"
                    "    from fi_instrumentation import register\n"
                )
            },
            "none",
        ),
        (
            {
                "agent.py": (
                    "import typing\n"
                    "if typing.TYPE_CHECKING:\n"
                    "    import traceai_langchain\n"
                    "else:\n"
                    "    import json\n"
                )
            },
            "none",
        ),
        (
            {
                "agent.py": (
                    "from typing import TYPE_CHECKING\n"
                    "if TYPE_CHECKING:\n"
                    "    import json\n"
                    "else:\n"
                    "    import traceai_openai\n"
                )
            },
            "traceai",
        ),
        (
            {
                "vendor/somelib/__init__.py": "import fi_instrumentation\n",
                "vendor/somelib-1.0.dist-info/METADATA": "Name: somelib\n",
                "agent.py": "import somelib\n",
            },
            "none",
        ),
    ],
    ids=[
        "local_module_named_like_traceai",
        "top_level_package_shadowing_opentelemetry",
        "nested_package_named_like_opentelemetry_shadows_nothing",
        "top_level_module_shadowing_opentelemetry",
        "nested_module_named_like_opentelemetry_shadows_nothing",
        "plain_folder_named_like_a_package_is_not_local",
        "type_checking_import",
        "typing_dot_type_checking_import",
        "type_checking_else_branch_counts",
        "vendored_install_folder_pruned",
    ],
)
def test_guards_against_imports_that_configure_nothing(
    tmp_path: Path, files: dict[str, str], expected: str
) -> None:
    assert detect_instrumentation(_repository(tmp_path, files)) == expected


def _generated_sum(terms: int) -> str:
    return "total = " + " + ".join(["value"] * terms) + "\n"


def test_a_deeply_nested_file_is_scanned_without_raising(tmp_path: Path) -> None:
    source = _repository(
        tmp_path,
        {"generated.py": "import traceai_openai\n" + _generated_sum(1200)},
    )

    assert detect_instrumentation(source) == "traceai"


def test_a_deeply_nested_file_does_not_stop_the_contract_being_written(
    tmp_path: Path,
) -> None:
    source = _repository(
        tmp_path / "source",
        {"agent.py": "import traceai_openai\n", "generated.py": _generated_sum(1200)},
    )
    payload = {
        **json.loads(RECORDED_CONTRACT.read_text(encoding="utf-8")),
        "instrumentation": "none",
    }

    result = accept_contract(payload, tmp_path / "out", source_root=source)

    saved = json.loads((tmp_path / "out" / "contract.json").read_text(encoding="utf-8"))
    assert not result.get("is_error"), result
    assert saved["instrumentation"] == "traceai"


def test_a_file_the_parser_cannot_nest_through_counts_for_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _repository(
        tmp_path, {"agent.py": "import traceai_openai\n", "deep.py": "x = 1\n"}
    )
    real_parse = ast.parse

    def parse(text: str, *args: object, **kwargs: object) -> ast.AST:
        if text == "x = 1\n":
            raise RecursionError
        return real_parse(text, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", parse)

    assert detect_instrumentation(source) == "traceai"


def test_a_link_to_a_file_without_end_is_read_only_to_a_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import os

    source = _repository(
        tmp_path, {"agent.py": "x = 1\n", "zero.py": "", "big.py": ""}
    )
    os.mkfifo(source / "pipe.py")

    class Endless(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:
            assert size >= 0, "read to the end"
            return b"#" * size

    opened = Path.open

    def open_(path: Path, *args: object, **kwargs: object):
        # Like a link to /dev/zero; and a pipe must never be opened, as that waits for a writer.
        assert path.name != "pipe.py", "opened a pipe"
        return Endless() if path.name == "zero.py" else opened(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_)
    # Past the limit a file is skipped, as one that doesn't parse is.
    (source / "big.py").write_text(_padded("import traceai_openai\n", LARGEST_PARSED_MODULE + 1))

    assert detect_instrumentation(source) == "none"


def _padded(line: str, size: int) -> str:
    return line + "#" * (size - len(line.encode()))


@pytest.mark.parametrize(
    "size, found", [(LARGEST_PARSED_MODULE, "traceai"), (LARGEST_PARSED_MODULE + 1, "none")]
)
def test_a_file_is_parsed_up_to_the_limit_and_skipped_past_it(
    tmp_path: Path, size: int, found: str
) -> None:
    source = _repository(tmp_path, {"agent.py": _padded("import traceai_openai\n", size)})

    assert detect_instrumentation(source) == found


def test_only_files_naming_what_the_scan_looks_for_are_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _repository(
        tmp_path,
        {
            "a.py": "import os\n",
            "b.py": "import traceai_openai\n",
            "c.py": "from fi_instrumentation import register\n",
            "d.py": "from opentelemetry.sdk.trace import TracerProvider\n",
        },
    )
    seen: list[str] = []
    real = instrumentation_evidence.parsed_or_syntax_error
    monkeypatch.setattr(
        instrumentation_evidence,
        "parsed_or_syntax_error",
        lambda text: seen.append(text) or real(text),
    )

    assert detect_instrumentation(source) == "traceai"
    assert sorted(seen) == [
        "from fi_instrumentation import register\n",
        "from opentelemetry.sdk.trace import TracerProvider\n",
        "import traceai_openai\n",
    ]


@pytest.mark.parametrize("total, found", [(22, "traceai"), (21, "none")])
def test_the_scan_parses_only_up_to_its_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, total: int, found: str
) -> None:
    source = _repository(tmp_path, {"agent.py": "import traceai_openai\n"})
    monkeypatch.setattr(python_source, "LARGEST_PARSED_TOTAL", total)

    assert detect_instrumentation(source) == found


def test_a_file_that_runs_the_parser_out_of_memory_counts_for_nothing(tmp_path: Path) -> None:
    # The parser's own stack overflows on this, as MemoryError, on 3.11 and 3.12.
    source = _repository(
        tmp_path,
        {"agent.py": "import traceai_openai\nx = " + "-" * 7000 + "1\n", "b.py": "import os\n"},
    )
    with pytest.raises(MemoryError):
        ast.parse((source / "agent.py").read_text())

    assert detect_instrumentation(source) == "none"


@pytest.mark.parametrize("listed", [list, lambda names: list(reversed(names))])
def test_past_the_total_the_answer_does_not_depend_on_how_the_disk_lists_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, listed
) -> None:
    import os

    source = _repository(
        tmp_path,
        {
            "a/agent.py": "import traceai_openai\n",
            "a/b.py": "# opentelemetry, someday\n",
            "z/agent.py": "# opentelemetry, someday\n",
        },
    )
    walk = os.walk

    def listed_walk(root):
        for directory, subdirectories, files in walk(root):
            subdirectories[:] = listed(sorted(subdirectories))
            yield directory, subdirectories, listed(sorted(files))

    monkeypatch.setattr(instrumentation_evidence.os, "walk", listed_walk)
    monkeypatch.setattr(python_source, "LARGEST_PARSED_TOTAL", 30)

    assert detect_instrumentation(source) == "traceai"


def test_a_file_with_a_byte_order_mark_is_read_as_python_reads_it(tmp_path: Path) -> None:
    source = tmp_path
    (source / "agent.py").write_bytes(b"\xef\xbb\xbfimport traceai_openai\n")

    assert detect_instrumentation(source) == "traceai"


def test_a_file_with_a_byte_order_mark_and_a_coding_line_is_read(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_bytes(b"\xef\xbb\xbf# coding: utf-8\nimport traceai_openai\n")

    assert detect_instrumentation(tmp_path) == "traceai"


@pytest.mark.parametrize("coding", ["rot13", "nosuch"])
def test_a_coding_line_python_cant_read_leaves_the_file_unread(tmp_path: Path, coding) -> None:
    (tmp_path / "agent.py").write_bytes(f"# coding: {coding}\nimport traceai_openai\n".encode())

    assert detect_instrumentation(tmp_path) != "traceai"


def test_a_fullwidth_name_is_a_known_limit(tmp_path: Path) -> None:
    # Python folds `ｔraceai_openai` to traceai_openai; the scan matches names as written.
    source = _repository(tmp_path, {"agent.py": "import ｔraceai_openai\n"})

    assert detect_instrumentation(source) != "traceai"


def test_an_import_spelled_in_escapes_is_found_under_unicode_escape(tmp_path: Path) -> None:
    # Python decodes the file before it reads it, so this runs `import traceai_openai`.
    (tmp_path / "agent.py").write_bytes(b"# coding: unicode_escape\nimport \\x74raceai_openai\n")
    assert detect_instrumentation(tmp_path) == "traceai"


def _package(dependencies: dict[str, str], field: str = "dependencies") -> str:
    return json.dumps({"name": "agent", field: dependencies})


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"package.json": _package({"@traceai/fi-core": "^1.4.1"})}, "traceai"),
        ({"package.json": _package({"@traceai/openai": "1.0.0"}, "devDependencies")}, "traceai"),
        (
            {
                "package.json": _package(
                    {"@future-agi/omega-runtime": "file:./vendor/omega-runtime-0.0.2.tgz"}
                )
            },
            "traceai",
        ),
        ({"package.json": _package({"@future-agi/other": "1.0.0"})}, "none"),
        (
            {
                "package.json": _package(
                    {
                        "@opentelemetry/sdk-trace-node": "^2.0.0",
                        "@opentelemetry/exporter-trace-otlp-http": "^0.200.0",
                    }
                )
            },
            "opentelemetry",
        ),
        (
            {
                "package.json": _package(
                    {
                        "@opentelemetry/sdk-node": "^0.200.0",
                        "@opentelemetry/exporter-trace-otlp-http": "^0.200.0",
                    }
                )
            },
            "opentelemetry",
        ),
        # Spans the hook cannot stamp with a call: protobuf, gRPC, or the SDK's default exporter.
        (
            {
                "package.json": _package(
                    {
                        "@opentelemetry/sdk-trace-node": "^2.0.0",
                        "@opentelemetry/exporter-trace-otlp-proto": "^0.200.0",
                        "@opentelemetry/exporter-trace-otlp-grpc": "^0.200.0",
                    }
                )
            },
            "none",
        ),
        ({"package.json": _package({"@opentelemetry/sdk-node": "^0.200.0"})}, "none"),
        # The browser SDK traces a page, not the agent.
        (
            {
                "package.json": _package(
                    {
                        "@opentelemetry/sdk-trace-web": "^2.0.0",
                        "@opentelemetry/exporter-trace-otlp-http": "^0.200.0",
                    }
                )
            },
            "none",
        ),
        ({"package.json": _package({"openai": "^4.0.0"})}, "none"),
        ({"package.json": "{not json"}, "none"),
        ({"package.json": "[]"}, "none"),
        ({"package.json": json.dumps({"dependencies": ["@traceai/fi-core"]})}, "none"),
        # Only the folder's own manifest counts, never an installed package's.
        (
            {"node_modules/x/package.json": _package({"@traceai/fi-core": "1.0.0"})},
            "none",
        ),
    ],
    ids=[
        "traceai",
        "traceai_dev_dependency",
        "omega_runtime_tarball",
        "other_future_agi_package",
        "opentelemetry_json",
        "opentelemetry_sdk_node_json",
        "protobuf_and_grpc_only",
        "sdk_node_default_exporter",
        "web_sdk",
        "untraced",
        "unparseable",
        "not_an_object",
        "dependencies_not_a_map",
        "installed_package",
    ],
)
def test_a_folder_without_python_is_read_from_its_package_json(
    tmp_path: Path, files: dict[str, str], expected: str
) -> None:
    assert detect_instrumentation(_repository(tmp_path, files)) == expected


def test_a_python_agent_beside_a_traced_frontend_keeps_its_answer(tmp_path: Path) -> None:
    traced_frontend = _package({"@traceai/fi-core": "^1.4.1"})
    untraced = _repository(
        tmp_path / "untraced", {"agent.py": "import openai\n", "package.json": traced_frontend}
    )
    assert detect_instrumentation(untraced) == "none"
    otel = _repository(
        tmp_path / "otel",
        {
            "agent.py": "from opentelemetry.sdk.trace import TracerProvider\n",
            "package.json": traced_frontend,
        },
    )
    assert detect_instrumentation(otel) == "opentelemetry"


def test_a_package_json_that_is_a_link_or_too_large_is_not_read(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(_package({"@traceai/fi-core": "1.0.0"}), encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "package.json").symlink_to(elsewhere)
    assert detect_instrumentation(linked) == "none"

    large = tmp_path / "large"
    large.mkdir()
    padding = " " * (instrumentation_evidence._LARGEST_PACKAGE_JSON)
    (large / "package.json").write_text(
        _package({"@traceai/fi-core": "1.0.0"}) + padding, encoding="utf-8"
    )
    assert detect_instrumentation(large) == "none"


def _saved_instrumentation(
    tmp_path: Path, files: dict[str, str], runtime: dict, subfolder: str | None = None
) -> str:
    source = _repository(tmp_path / "source", files)
    recorded = json.loads(RECORDED_CONTRACT.read_text(encoding="utf-8"))
    payload = {
        **recorded,
        "runtime": {**recorded["runtime"], **runtime},
        "instrumentation": "none",
    }
    result = accept_contract(payload, tmp_path / "out", source_root=source, subfolder=subfolder)
    assert not result.get("is_error"), result
    saved = json.loads((tmp_path / "out" / "contract.json").read_text(encoding="utf-8"))
    return saved["instrumentation"]


_NODE_RUNTIME = {"language": "node", "command": ["node", "index.js"]}
_WORKSPACE_ROOT = json.dumps({"name": "repo", "private": True, "workspaces": ["agent"]})


def test_a_node_agent_with_a_python_helper_is_read_from_its_package_json(tmp_path: Path) -> None:
    """The selected runtime decides the reader: a build script in Python does not make it a Python agent."""
    files = {
        "package.json": _package({"@traceai/fi-core": "^1.4.1"}),
        "index.js": "require('http');\n",
        "scripts/build.py": "import json\n",
    }

    assert _saved_instrumentation(tmp_path, files, _NODE_RUNTIME) == "traceai"


@pytest.mark.parametrize(
    "layout",
    ["member_declares", "hoisted_to_the_workspace_root"],
)
def test_a_node_agent_is_read_from_its_workdir_and_its_package_root(tmp_path: Path, layout: str) -> None:
    """runtime.workdir's package.json, and the workspace root above it, where a tracing package may be hoisted."""
    traced = {"@traceai/fi-core": "^1.4.1"}
    member, root = (traced, {}) if layout == "member_declares" else ({}, traced)
    files = {
        "package.json": json.dumps({**json.loads(_WORKSPACE_ROOT), "dependencies": root}),
        "agent/package.json": _package(member),
        "agent/index.js": "require('http');\n",
    }

    assert _saved_instrumentation(tmp_path, files, {**_NODE_RUNTIME, "workdir": "agent"}) == "traceai"


def test_a_node_agent_in_a_subfolder_counts_the_workspace_roots_packages(tmp_path: Path) -> None:
    files = {
        "package.json": json.dumps(
            {**json.loads(_WORKSPACE_ROOT), "dependencies": {"@traceai/fi-core": "^1.4.1"}}
        ),
        "agent/package.json": _package({}),
        "agent/index.js": "require('http');\n",
    }

    assert _saved_instrumentation(tmp_path, files, _NODE_RUNTIME, subfolder="agent") == "traceai"


def test_a_python_runtime_keeps_the_answer_its_imports_give(tmp_path: Path) -> None:
    files = {"agent.py": "import openai\n", "package.json": _package({"@traceai/fi-core": "^1.4.1"})}

    assert _saved_instrumentation(tmp_path, files, {"language": "python"}) == "none"


def test_a_node_agent_is_not_read_past_its_package_root(tmp_path: Path) -> None:
    """A folder above the workspace root is another project: its packages are not the agent's."""
    files = {
        "package.json": _package({"@traceai/fi-core": "^1.4.1"}),
        "app/package.json": _WORKSPACE_ROOT,
        "app/agent/package.json": _package({}),
    }

    assert instrumentation_evidence.detect_node_instrumentation(
        _repository(tmp_path, files), "app/agent"
    ) == "none"


def test_a_manifest_with_both_sdks_is_traceai(tmp_path: Path) -> None:
    both = {
        "@traceai/fi-core": "^1.4.1",
        "@opentelemetry/sdk-trace-node": "^1.0.0",
        "@opentelemetry/exporter-trace-otlp-http": "^0.50.0",
    }

    assert detect_instrumentation(_repository(tmp_path, {"package.json": _package(both)})) == "traceai"


_INFERRED = {"language": "", "command": [], "workdir": "agent"}


def test_a_node_agent_known_only_from_its_dockerfile_is_read_from_its_package_json(
    tmp_path: Path,
) -> None:
    """No language and no command: the workdir Dockerfile's CMD is what makes it Node, as the planner reads it."""
    files = {
        "helper.py": "import json\n",
        "agent/package.json": _package({"@future-agi/omega-runtime": "^0.4.0"}),
        "agent/server.js": "require('http');\n",
        "agent/Dockerfile": 'FROM node:22\nCMD ["node", "server.js"]\n',
    }

    assert _saved_instrumentation(tmp_path, files, _INFERRED) == "traceai"


def test_a_root_dockerfile_cmd_decides_before_the_workdirs(tmp_path: Path) -> None:
    """The planner reads the root Dockerfile first; a Python CMD there keeps the Python reading."""
    files = {
        "helper.py": "import json\n",
        "Dockerfile": 'FROM python:3.12\nCMD ["python", "helper.py"]\n',
        "agent/package.json": _package({"@future-agi/omega-runtime": "^0.4.0"}),
        "agent/Dockerfile": 'FROM node:22\nCMD ["node", "server.js"]\n',
    }

    assert _saved_instrumentation(tmp_path, files, _INFERRED) == "none"


def test_an_independent_package_under_a_traced_parent_is_read_alone(tmp_path: Path) -> None:
    """A parent manifest that does not declare the agent's package in its workspaces is another project's."""
    files = {
        "package.json": _package({"@traceai/fi-core": "^1.4.1"}),
        "agent/package.json": _package({}),
        "agent/index.js": "require('http');\n",
    }

    assert _saved_instrumentation(tmp_path, files, {**_NODE_RUNTIME, "workdir": "agent"}) == "none"


@pytest.mark.parametrize(
    ("workspaces", "expected"),
    [
        (["packages/*"], "traceai"),
        ({"packages": ["packages/**"]}, "traceai"),
        (["./packages/agent/"], "traceai"),
        (["apps/*"], "none"),
    ],
    ids=["glob", "yarn_object", "spelled_path", "other_members"],
)
def test_a_workspace_root_counts_only_for_a_member_it_declares(
    tmp_path: Path, workspaces: object, expected: str
) -> None:
    files = {
        "package.json": json.dumps(
            {"name": "repo", "workspaces": workspaces, "dependencies": {"@traceai/fi-core": "^1.4.1"}}
        ),
        "packages/agent/package.json": _package({}),
    }

    assert (
        instrumentation_evidence.detect_node_instrumentation(
            _repository(tmp_path, files), "packages/agent"
        )
        == expected
    )


def test_an_unread_manifest_ends_the_walk(tmp_path: Path) -> None:
    """A package.json past the size limit is still the agent's package: the walk stops there."""
    padding = " " * instrumentation_evidence._LARGEST_PACKAGE_JSON
    files = {
        "package.json": _package({"@traceai/fi-core": "^1.4.1"}),
        "agent/package.json": _package({}) + padding,
    }

    assert (
        instrumentation_evidence.detect_node_instrumentation(_repository(tmp_path, files), "agent/src")
        == "none"
    )


@pytest.mark.parametrize("language", ["typescript", "javascript", "js", "ts", "nodejs"])
def test_a_language_the_planner_reads_as_node_is_read_from_its_package_json(
    tmp_path: Path, language: str
) -> None:
    files = {"package.json": _package({}), "agent/package.json": _package({"@traceai/fi-core": "^1.4.1"})}

    runtime = {"language": language, "command": ["node", "index.js"], "workdir": "agent"}
    assert _saved_instrumentation(tmp_path, files, runtime) == "traceai"


def test_the_root_workdir_sentinel_reads_the_root_manifest(tmp_path: Path) -> None:
    """The planner reads workdir "/" as the repository root."""
    files = {"package.json": _package({"@traceai/fi-core": "^1.4.1"}), "index.js": "require('http');\n"}

    assert _saved_instrumentation(tmp_path, files, {**_NODE_RUNTIME, "workdir": "/"}) == "traceai"


def test_a_single_nested_node_component_the_planner_infers_is_read_from_its_package_json(
    tmp_path: Path,
) -> None:
    """No language, no command, no Dockerfile: the planner picks the one component in the repository."""
    files = {
        "agent/package.json": _package({"@traceai/fi-core": "^1.4.1"}),
        "agent/index.js": "require('http');\n",
    }

    runtime = {"language": "", "command": [], "workdir": "", "dockerfile": "", "compose_file": ""}
    assert _saved_instrumentation(tmp_path, files, runtime) == "traceai"


_PYTHON_WITHOUT_READABLE_SOURCE = {
    "package.json": _package({"@traceai/fi-core": "^1.4.1"}),
    "pyproject.toml": "[project]\nname = 'agent'\n",
    "build/agent.py": "import json\n",
}


def test_an_explicit_python_runtime_never_reads_package_json(tmp_path: Path) -> None:
    runtime = {"language": "python", "command": ["python", "-m", "agent"]}

    assert _saved_instrumentation(tmp_path, _PYTHON_WITHOUT_READABLE_SOURCE, runtime) == "none"


def _session_saved(tmp_path: Path, files: dict[str, str], runtime: dict, cached: str) -> str:
    """The save as the save child runs it: with the session's early answer, read before the runtime was known."""
    source = _repository(tmp_path / "source", files)
    recorded = json.loads(RECORDED_CONTRACT.read_text(encoding="utf-8"))
    payload = {**recorded, "runtime": {**recorded["runtime"], **runtime}}
    checked = tools._checked_contract(
        payload,
        source_root=source,
        require_setup_contract=False,
        subfolder=None,
        instrumentation=cached,
    )
    assert not isinstance(checked, dict), checked
    return checked.instrumentation


def test_the_sessions_early_answer_is_replaced_for_a_node_runtime(tmp_path: Path) -> None:
    files = {"package.json": _package({"@traceai/fi-core": "^1.4.1"}), "scripts/build.py": "import json\n"}

    assert _session_saved(tmp_path, files, _NODE_RUNTIME, cached="none") == "traceai"


def test_the_sessions_manifest_answer_is_dropped_for_a_python_runtime(tmp_path: Path) -> None:
    """The session read package.json because it found no Python file; a Python runtime never counts it."""
    runtime = {"language": "python", "command": ["python", "-m", "agent"]}

    assert _session_saved(tmp_path, _PYTHON_WITHOUT_READABLE_SOURCE, runtime, cached="traceai") == "none"


def test_the_sessions_python_answer_is_kept_for_a_python_runtime(tmp_path: Path) -> None:
    files = {"agent.py": "from fi_instrumentation import register\n"}
    runtime = {"language": "python", "command": ["python", "agent.py"]}

    assert _session_saved(tmp_path, files, runtime, cached="traceai") == "traceai"
