#!/usr/bin/env python3
"""Run the installed ALK quickstart without credentials or network access."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any


_ENV_ALLOWLIST = {
    "COMSPEC",
    "LD_LIBRARY_PATH",
    "PATH",
    "PATHEXT",
    "PYTHONPATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
}

_PUBLIC_IMPORTS = (
    "fi.alk.capabilities",
    "fi.alk.simulate",
    "fi.alk.evals",
    "fi.alk.redteam",
    "fi.alk.optimize",
    "fi.alk.suite",
)


def _block_network_and_processes(attempts: list[Any]) -> None:
    def blocked_socket(_socket: socket.socket, *args: Any, **kwargs: Any) -> None:
        target = args[-1] if args else kwargs
        attempts.append(("socket", target))
        raise RuntimeError(
            f"offline quickstart attempted a network operation: {target}"
        )

    def blocked_connection(address: Any, *args: Any, **kwargs: Any) -> None:
        attempts.append(("connection", address))
        raise RuntimeError(
            f"offline quickstart attempted a network connection: {address}"
        )

    def blocked_process(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else kwargs.get("args")
        attempts.append(("subprocess", command))
        raise RuntimeError(
            f"offline quickstart attempted to start a subprocess: {command}"
        )

    socket.create_connection = blocked_connection
    socket.socket.connect = blocked_socket
    socket.socket.connect_ex = blocked_socket
    socket.socket.sendto = blocked_socket
    if hasattr(socket.socket, "sendmsg"):
        socket.socket.sendmsg = blocked_socket
    subprocess.Popen = blocked_process


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-wheel", action="store_true")
    parser.add_argument(
        "--probe-startup-network", action="store_true", help=argparse.SUPPRESS
    )
    args = parser.parse_args()

    environment = {
        name: os.environ[name] for name in _ENV_ALLOWLIST if name in os.environ
    }
    os.environ.clear()
    os.environ.update(environment)

    with tempfile.TemporaryDirectory(prefix="alk-offline-quickstart-") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        os.environ.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")})
        os.chdir(root)

        console_script = shutil.which(
            "agent-learn", path=str(Path(sys.executable).parent)
        )
        assert console_script is not None, "agent-learn is not installed"
        attempts: list[Any] = []
        _block_network_and_processes(attempts)

        if args.probe_startup_network:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.sendto(b"offline guard probe", ("127.0.0.1", 9))

        previous_argv = sys.argv
        help_stdout = io.StringIO()
        try:
            sys.argv = [console_script, "--help"]
            with contextlib.redirect_stdout(help_stdout):
                try:
                    runpy.run_path(console_script, run_name="__main__")
                except SystemExit as exc:
                    assert exc.code in (None, 0), exc.code
        finally:
            sys.argv = previous_argv
        assert "Unified CLI for Future AGI agent simulation" in help_stdout.getvalue()

        import fi.alk
        from fi.alk.cli import main as alk_main

        module_path = Path(fi.alk.__file__).resolve()
        if args.expect_wheel and not {"site-packages", "dist-packages"}.intersection(
            module_path.parts
        ):
            raise AssertionError(f"expected an installed wheel, imported {module_path}")

        for module_name in _PUBLIC_IMPORTS:
            importlib.import_module(module_name)

        project = root / "quickstart"
        assert alk_main(["doctor", "--quiet"]) == 0
        assert alk_main(["init", str(project), "--preset", "run", "--quiet"]) == 0
        artifact_path = project / "artifacts" / "run.json"
        assert (
            alk_main(
                [
                    "run",
                    str(project / "manifests" / "run.json"),
                    "--output",
                    str(artifact_path),
                ]
            )
            == 0
        )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["kind"] == "agent-learning.run.v1"
        assert artifact["status"] == "passed"
        from fi.alk.telemetry import flush

        if not flush():
            raise AssertionError("offline quickstart telemetry did not finish writing")

    if attempts:
        raise AssertionError(
            f"offline quickstart attempted forbidden operations: {attempts}"
        )
    print(f"alk_module={module_path}")
    print(f"public_imports={','.join(_PUBLIC_IMPORTS)}")
    print("console=agent-learn --help status=passed")
    print("artifact=agent-learning.run.v1 status=passed network_attempts=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
