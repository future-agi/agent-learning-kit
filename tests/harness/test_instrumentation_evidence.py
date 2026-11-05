from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from fi.alk.harness import instrumentation_evidence, python_source
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
    real = instrumentation_evidence.parsed
    monkeypatch.setattr(
        instrumentation_evidence, "parsed", lambda text: seen.append(text) or real(text)
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


def test_a_fullwidth_name_is_read_as_python_reads_it(tmp_path: Path) -> None:
    source = _repository(tmp_path, {"agent.py": "import ｔraceai_openai\n"})

    assert detect_instrumentation(source) == "traceai"
