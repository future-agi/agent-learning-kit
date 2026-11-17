from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fi.alk.harness import instrumentation_evidence
from fi.alk.harness.instrumentation_evidence import detect_instrumentation
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
            "none",
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


def _detect_in_a_child(source: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "from fi.alk.harness.instrumentation_evidence import detect_instrumentation; "
            "print(detect_instrumentation(Path(sys.argv[1])))",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_a_file_with_a_byte_order_mark_is_read(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_bytes(b"\xef\xbb\xbfimport traceai_openai\n")

    assert detect_instrumentation(tmp_path) == "traceai"


def test_a_latin_1_file_with_a_coding_line_is_read(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_bytes(
        "# -*- coding: latin-1 -*-\nimport traceai_openai\nNAME = 'caf\u00e9'\n".encode(
            "latin-1"
        )
    )

    assert detect_instrumentation(tmp_path) == "traceai"


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW") or not Path("/dev/zero").exists(),
    reason="needs O_NOFOLLOW and /dev/zero",
)
def test_a_symlink_to_a_device_is_not_read(tmp_path: Path) -> None:
    source = _repository(tmp_path, {"agent.py": "import traceai_openai\n"})
    (source / "zero.py").symlink_to("/dev/zero")

    assert _detect_in_a_child(source) == "traceai"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_named_pipe_is_not_waited_on(tmp_path: Path) -> None:
    source = _repository(tmp_path, {"agent.py": "import traceai_openai\n"})
    os.mkfifo(source / "pipe.py")

    assert _detect_in_a_child(source) == "traceai"


def test_running_out_of_memory_on_one_file_keeps_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _repository(
        tmp_path, {"agent.py": "import traceai_openai\n", "huge.py": "x = 1\n"}
    )
    real_parse = ast.parse

    def parse(text: str, *args: object, **kwargs: object) -> ast.AST:
        if text == "x = 1\n":
            raise MemoryError
        return real_parse(text, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", parse)

    assert detect_instrumentation(source) == "traceai"


def test_a_file_with_an_overlong_line_is_skipped(tmp_path: Path) -> None:
    long_line = "DATA = '" + "a" * (64 * 1024 + 1) + "'\n"
    source = _repository(
        tmp_path, {"generated.py": "import traceai_openai\n" + long_line}
    )

    assert detect_instrumentation(source) == "none"


def test_a_file_larger_than_what_is_left_to_read_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _repository(tmp_path, {"agent.py": "import traceai_openai\n" + "#" * 100})

    monkeypatch.setattr(instrumentation_evidence, "_READ_LIMIT", 50)
    assert detect_instrumentation(source) == "none"
    monkeypatch.setattr(instrumentation_evidence, "_READ_LIMIT", 10_000)
    assert detect_instrumentation(source) == "traceai"


def test_reading_stops_at_the_file_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _repository(tmp_path, {"agent.py": "import traceai_openai\n"})

    monkeypatch.setattr(instrumentation_evidence, "_FILE_LIMIT", 0)
    assert detect_instrumentation(source) == "none"
    monkeypatch.setattr(instrumentation_evidence, "_FILE_LIMIT", 1)
    assert detect_instrumentation(source) == "traceai"
