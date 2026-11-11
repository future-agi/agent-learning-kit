"""TH-9042: one list of credential files, kept from the model on every reader.

The list is `files.SECRET_GLOBS`. The kit's own tools, the scan's tools and the gate match paths
against the regex built from it; Claude Code gets it as Read deny rules, which its own Grep and
Glob turn into exclusions. The last test runs the real bundled CLI against a local stand-in for
the Messages API (no model, no key) and checks that it hides exactly what the kit hides.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness.backends import files
from fi.alk.harness.backends.base import SessionSpec, WorkerSpec
from fi.alk.harness.backends.files import (
    ALL_SECRET_GLOBS,
    SECRET_GLOBS,
    file_tools,
    secret_path,
    secret_read_rules,
    secret_text,
)

# Written out by hand, never built from the module's list: removing a glob there must fail here.
# Each sample is matched by its own glob and no other.
TABLE: tuple[tuple[str, str], ...] = (
    ("**/.env*", ".envrc"),
    ("**/*.env", "deploy/prod.env"),
    ("**/*.env.*", "deploy/prod.env.example"),
    ("**/.claude", ".claude/settings.json"),
    ("**/.ssh", ".ssh/config"),
    ("**/.aws", ".aws/credentials"),
    ("**/.config/gcloud", ".config/gcloud/adc.json"),
    ("**/.authoring-credentials", ".authoring-credentials/g.json"),
    ("**/.netrc", ".netrc"),
    ("**/.npmrc", "web/.npmrc"),
    ("**/.pypirc", ".pypirc"),
    ("**/*credential*.json", "my-credentials.json"),
    ("**/*secret*.json", "client_secret.json"),
    ("**/*token*.json", "token.json"),
    ("**/*service-account*.json", "gcp-service-account.json"),
    ("**/*service_account*.json", "service_account.json"),
    ("**/*serviceaccount*.json", "serviceaccount.json"),
    ("**/*secret*.yaml", "config/secrets.yaml"),
    ("**/*secret*.yml", "k8s/db-secret.yml"),
    ("**/*secret*.toml", ".streamlit/secrets.toml"),
    ("**/*credential*.yaml", "credentials.yaml"),
    ("**/*credential*.yml", "config/credentials.yml"),
    ("**/*credential*.toml", "app-credentials.toml"),
    ("**/*secret*.ini", "config/secrets.ini"),
    ("**/*secret*.cfg", "secrets.cfg"),
    ("**/*secret*.conf", "etc/app-secrets.conf"),
    ("**/*secret*.properties", "secrets.properties"),
    ("**/*secret*.xml", "secrets.xml"),
    ("**/*credential*.ini", "credentials.ini"),
    ("**/*credential*.cfg", "conf/credentials.cfg"),
    ("**/*credential*.conf", "credentials.conf"),
    ("**/*credential*.properties", "src/main/resources/credentials.properties"),
    ("**/*credential*.xml", "jenkins/credentials.xml"),
    ("**/*firebase-adminsdk*.json", "app/myproj-firebase-adminsdk-ab12c-0123456789.json"),
    ("**/*credential*.csv", "aws/my_credentials.csv"),
    ("**/*accesskeys*.csv", "rootkey-accessKeys.csv"),
    ("**/*secret*.txt", "api-secret.txt"),
    ("**/*credential*.txt", "gcp-credential.txt"),
    ("**/secrets/*.txt", "deploy/secrets/db_password.txt"),
    ("**/.secrets", ".secrets"),
    ("**/.dev.vars", "worker/.dev.vars"),
    ("**/local.settings.json", "functions/local.settings.json"),
    ("**/key.properties", "android/key.properties"),
    ("**/keystore.properties", "android/keystore.properties"),
    ("**/AuthKey_*.p8", "AuthKey_ABC123.p8"),
    ("**/*.ppk", "deploy/server.ppk"),
    ("**/.config/gh/hosts.yml", ".config/gh/hosts.yml"),
    ("**/.databrickscfg", ".databrickscfg"),
    ("**/.terraformrc", ".terraformrc"),
    ("**/.cargo/credentials", ".cargo/credentials"),
    ("**/.gem/credentials", ".gem/credentials"),
    ("**/*.pem", "certs/server.pem"),
    ("**/*.key", "certs/server.key"),
    ("**/*.p12", "certs/a.p12"),
    ("**/*.pfx", "certs/a.pfx"),
    ("**/*.jks", "android/release.jks"),
    ("**/*.keystore", "android/debug.keystore"),
    # The ticket's six: .kube/config, .docker/config.json, .pgpass, .htpasswd, terraform.tfstate
    # and *-key.json.
    ("**/.kube/config", ".kube/config"),
    ("**/.kube/config.*", ".kube/config.prod"),
    ("**/kubeconfig", "deploy/kubeconfig"),
    ("**/kubeconfig.yaml", "kubeconfig.yaml"),
    ("**/kubeconfig.yml", "kubeconfig.yml"),
    ("**/*.kubeconfig", "admin.kubeconfig"),
    ("**/.docker/config.json", ".docker/config.json"),
    ("**/.docker/contexts", ".docker/contexts/meta/m.json"),
    ("**/.dockercfg", ".dockercfg"),
    ("**/.pgpass", ".pgpass"),
    ("**/.htpasswd", "web/.htpasswd"),
    ("**/.git-credentials", ".git-credentials"),
    ("**/.my.cnf", ".my.cnf"),
    ("**/.s3cfg", ".s3cfg"),
    ("**/.boto", ".boto"),
    ("**/.vault-token", ".vault-token"),
    ("**/id_rsa", "deploy/id_rsa"),
    ("**/id_dsa", "id_dsa"),
    ("**/id_ecdsa", "id_ecdsa"),
    ("**/id_ed25519", "keys/id_ed25519"),
    ("**/id_ecdsa_sk", "id_ecdsa_sk"),
    ("**/id_ed25519_sk", "id_ed25519_sk"),
    ("**/*.tfstate", "infra/terraform.tfstate"),
    ("**/*.tfstate.backup", "infra/terraform.tfstate.backup"),
    ("**/*.tfstate.*.backup", "infra/terraform.tfstate.1700000000.backup"),
    ("**/*.tfvars", "infra/prod.tfvars"),
    ("**/*.tfvars.json", "infra/prod.tfvars.json"),
    ("**/*-key.json", "gcp-key.json"),
    ("**/*_key.json", "api_key.json"),
    ("**/*-sa.json", "deploy/gcp-sa.json"),
)
# Exact names whose .bak copy is listed too.
BACKED_UP = (
    "deploy/kubeconfig", "kubeconfig.yaml", "kubeconfig.yml", "admin.kubeconfig",
    ".docker/config.json", ".dockercfg", ".pgpass", "web/.htpasswd", ".git-credentials",
    ".my.cnf", ".s3cfg", ".boto", ".vault-token", "deploy/id_rsa", "id_dsa", "id_ecdsa",
    "keys/id_ed25519", "id_ecdsa_sk", "id_ed25519_sk",
)  # fmt: skip
NEAR_MISSES = (
    ".dockerignore", ".kubeignore", ".docker/Dockerfile", ".docker/compose.yml",
    ".docker/contexts-notes.md", ".docker/config.json.example", "x.docker/config.json",
    ".kube/configmap.yaml", ".kube/configs/a.yaml", ".kube/cache/x", "x.kube/config",
    "kubeconfig.go", "xkubeconfig", "my-kubeconfig-docs.md", "x.kubeconfig.md",
    ".dockercfg.example", "x.dockercfg", "valid_rsa", "id_rsa.pub", "my.pgpass",
    ".htpasswd.example", "my.boto", ".boto.cfg", ".vault-token.md",
    "infra/terraform.tfstate.lock.info", "infra/terraform.tfstate.backup.old",
    "infra/terraform.tfvars.example", "debug.keystore.md", "key.json", "gcp-key.json.example",
    ".azure/pipelines.yml", "secrets/app.json", "tokens/list.json", "credentials/README.json",
    "infra/terraform.tfstate.d/default.backup", "agent.py",
    "src/env.mjs", "next-env.d.ts", "vite-env.d.ts", "env.example", "dotenv.py",
    "secret_manager.py", "credentials_helper.go", "secrets.yaml.example", "visa.json", "sa.json",
    "secrets.py", "secrets.sh", "firebase.json", "id_ed25519.pub", "secrets.ini.example",
    "secrets/manager.go", "credentials/README.txt", ".cargo/config.toml", "gh/hosts.yml", "AuthKey.md",
)  # fmt: skip


# What the scan hides beyond the shared list, written out by hand: every name with "secret" or
# "credential" in it, such as source files and source folders a build reads, and every SSH key's
# .pub. A name the scan starts or stops hiding fails the end-to-end test.
SCAN_ONLY = (
    "id_rsa.pub", "secrets/app.json", "credentials/README.json", "secret_manager.py",
    "credentials_helper.go", "secrets.yaml.example", "secrets.py", "secrets.sh", "id_ed25519.pub",
    "secrets.ini.example", "secrets/manager.go", "credentials/README.txt",
)  # fmt: skip


def test_every_glob_is_one_the_cli_takes_as_written() -> None:
    assert SECRET_GLOBS == tuple(glob for glob, _ in TABLE)
    for glob in ALL_SECRET_GLOBS:
        assert glob.startswith("**/"), glob
        assert re.fullmatch(r"[A-Za-z0-9._*/-]+", glob), glob
    backups = ALL_SECRET_GLOBS[len(SECRET_GLOBS) :]
    assert all(glob.endswith(".bak") for glob in backups)
    assert len(backups) == len(BACKED_UP)


def test_a_glob_means_the_path_or_any_folder_above_it() -> None:
    assert files._glob_pattern("**/.pgpass") == r"(^|/)\.pgpass(/|$)"
    assert files._glob_pattern("**/*.tfstate") == r"(^|/)[^/]*\.tfstate(/|$)"
    assert files._glob_pattern("**/.kube/config.*") == r"(^|/)\.kube/config\.[^/]*(/|$)"


@pytest.mark.parametrize("glob, sample", TABLE, ids=[glob for glob, _ in TABLE])
def test_every_listed_name_is_refused_however_it_is_written(
    tmp_path: Path, glob: str, sample: str
) -> None:
    for written in (sample, f"a/b/{sample}", sample.upper(), f"/abs/x/{sample}", f"{sample}/inner"):
        assert secret_text(written), written
    others = [other for other in ALL_SECRET_GLOBS if other != glob]
    assert not re.search("|".join(files._glob_pattern(g) for g in others), sample, re.I), sample
    target = tmp_path / sample
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    (tmp_path / "plain.txt").symlink_to(target)
    (tmp_path / "plainfolder").symlink_to(target.parent)
    assert secret_path("plain.txt", tmp_path)
    assert secret_path(f"plainfolder/{target.name}", tmp_path)
    if sample in BACKED_UP:
        assert secret_text(f"{sample}.bak")


@pytest.mark.parametrize("name", NEAR_MISSES)
def test_near_misses_are_not_refused(name: str) -> None:
    assert not secret_text(name)
    assert not secret_text(f"a/{name}")


def test_every_claude_session_carries_the_rules() -> None:
    from fi.alk.harness.backends.claude import ClaudeBackend

    rules = [f"Read(//{glob})" for glob in ALL_SECRET_GLOBS]
    assert secret_read_rules() == rules
    for gated in (True, False):
        spec = SessionSpec(
            system_prompt="x", builtins=("Read", "Bash"), model="gemini-test", gated=gated
        )
        disallowed = ClaudeBackend()._options(spec).disallowed_tools
        assert disallowed[len(disallowed) - len(rules) :] == rules, gated
        assert "Bash" not in disallowed  # granted, so not disallowed
        if gated:
            assert "Write" in disallowed


# ---- The end-to-end test, with the real bundled CLI -------------------------------------------


class _FakeMessages:
    """A local stand-in for the Messages API. Each request is answered with the next scripted
    tool call for its session (one that offers `Task` is the parent; one that doesn't is the
    worker), then a plain end. Every request is kept, so the tool results can be read after."""

    def __init__(self, parent: list[dict], worker: list[dict], *, split: bool = True) -> None:
        self.split = split
        self.queues = {"parent": list(parent), "worker": list(worker)}
        self.served = {"parent": 0, "worker": 0}
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                self._json(b"{}")

            def _json(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                size = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(size) or b"{}")
                if "count_tokens" in self.path:
                    self._json(b'{"input_tokens": 10}')
                    return
                tools = {tool.get("name") for tool in body.get("tools") or []}
                role = "parent" if outer.split and tools & {"Task", "Agent"} else "worker"
                with outer.lock:
                    outer.requests.append({"role": role, "tools": sorted(tools), "body": body})
                    call = None
                    if tools & {"Grep", "Glob", "Read", "Task", "Agent"} and outer.queues[role]:
                        call = outer.queues[role].pop(0)
                        outer.served[role] += 1
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self._events(call, body.get("model", "fake"))

            def _events(self, call: dict | None, model: str) -> None:
                def send(event: str, data: dict) -> None:
                    self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())

                message = {
                    "id": "msg_fake", "type": "message", "role": "assistant", "model": model,
                    "content": [], "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                }  # fmt: skip
                send("message_start", {"type": "message_start", "message": message})
                if call is not None:
                    block = {"type": "tool_use", "id": call["id"], "name": call["name"], "input": {}}
                    delta = {"type": "input_json_delta", "partial_json": json.dumps(call["input"])}
                    stop = "tool_use"
                else:
                    block = {"type": "text", "text": ""}
                    delta = {"type": "text_delta", "text": "done"}
                    stop = "end_turn"
                send("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block})
                send("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta})
                send("content_block_stop", {"type": "content_block_stop", "index": 0})
                send("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 5}})
                send("message_stop", {"type": "message_stop"})
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "_FakeMessages":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def results(self) -> dict[str, tuple[str, str]]:
        """Each tool call's result text, and the role of the session that sent it back. An error
        result's text starts with "ERROR:", so a case can't pass on a call that failed."""
        found: dict[str, tuple[str, str]] = {}
        for request in self.requests:
            for message in request["body"].get("messages") or []:
                if message.get("role") != "user" or not isinstance(message.get("content"), list):
                    continue
                for block in message["content"]:
                    if block.get("type") == "tool_result":
                        text = json.dumps(block.get("content"))
                        if block.get("is_error"):
                            text = "ERROR:" + text
                        found[block["tool_use_id"]] = (text, request["role"])
        return found


_MARK = re.compile(r"MARK-[PN]\d+")


def _fixture(source: Path) -> tuple[dict[str, str], dict[str, str]]:
    """One fake file for each listed sample and each near miss, each with its own marker."""
    positives = {sample: f"MARK-P{n}" for n, (_, sample) in enumerate(TABLE)}
    positives.update({f"{sample}.bak": f"MARK-P{100 + n}" for n, sample in enumerate(BACKED_UP)})
    near = {name: f"MARK-N{n}" for n, name in enumerate(NEAR_MISSES)}
    for path, mark in {**positives, **near}.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{mark} password\n")
    (source / "k").symlink_to(".kube")
    return positives, near


def _expected(near: dict[str, str], root: str) -> set[str]:
    if root and secret_text(root):
        return set()
    return {mark for path, mark in near.items() if not root or path.startswith(f"{root}/")}


def _calls(source: Path, prefix: str) -> list[tuple[dict, str]]:
    """Each call and the folder (relative to the source) whose near misses it should show."""
    where = (lambda rel: str(source / rel) if rel else str(source)) if prefix else (lambda rel: rel or ".")
    calls = [
        ({"name": "Grep", "input": {"pattern": "password", "path": where(""), "output_mode": "content"}}, ""),
        ({"name": "Grep", "input": {"pattern": "password", "path": where("k"), "output_mode": "content"}}, ".kube"),
        ({"name": "Grep", "input": {"pattern": "password", "path": where(""), "glob": "**/.pgpass", "output_mode": "content"}}, "-"),
        ({"name": "Glob", "input": {"pattern": "**/*", "path": where("")}}, "glob"),
    ]
    for rel in (".kube", ".docker", ".docker/contexts", ".config", "infra"):
        calls.append(({"name": "Grep", "input": {"pattern": "password", "path": where(rel), "output_mode": "content"}}, rel))
    if not prefix:
        calls.append(({"name": "Grep", "input": {"pattern": "password", "output_mode": "content"}}, ""))
    return calls


def _run(options: Any) -> None:
    from claude_agent_sdk import query

    async def go() -> None:
        async for _ in query(prompt="go", options=options):
            pass

    asyncio.run(asyncio.wait_for(go(), 180))


def _versions() -> str:
    import claude_agent_sdk

    from claude_agent_sdk import _cli_version

    return f"sdk {claude_agent_sdk.__version__}, cli {getattr(_cli_version, '__cli_version__', '?')}"


@pytest.mark.parametrize("layout", ["source", "output"])
def test_claude_code_and_the_kit_hide_the_same_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    import claude_agent_sdk

    from fi.alk.harness import scan_ai
    from fi.alk.harness.backends.claude import ClaudeBackend

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    assert bundled.exists(), f"the bundled Claude Code CLI is missing ({_versions()})"
    source = tmp_path / "src"
    positives, near = _fixture(source)
    out = tmp_path / "out"
    out.mkdir()
    cwd = source if layout == "source" else out
    calls = _calls(source, "" if layout == "source" else "abs")
    for index, (call, _) in enumerate(calls):
        call["id"] = f"toolu_fake{index}"
    worker_call = dict(calls[0][0], id="toolu_worker")
    parent = [{"id": "toolu_task", "name": "Task", "input": {"subagent_type": "looker", "description": "look", "prompt": "go"}}]
    for name, value in {
        "HOME": str(tmp_path / "home"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "ALK_CLAUDE_GATEWAY_API_KEY": "local-fake-not-a-key",
    }.items():
        monkeypatch.setenv(name, value)
    (tmp_path / "home").mkdir()
    (tmp_path / "cfg").mkdir()

    with _FakeMessages(parent=[], worker=[c for c, _ in calls], split=False) as fake:
        monkeypatch.setenv("ALK_CLAUDE_GATEWAY_URL", fake.url)
        spec = SessionSpec(system_prompt="test", builtins=("Read", "Glob", "Grep"), cwd=str(cwd), model="gemini-test", max_turns=40)
        _run(ClaudeBackend()._options(spec))
        assert fake.served["worker"] == len(calls), (fake.served, _versions())
    results = fake.results()

    kit = {spec.name: spec.handler for spec in file_tools(str(source))}
    scan = {spec.name: spec.handler for spec in scan_ai.rooted_file_tools(source).tools}
    for call, root in calls:
        text, _ = results[call["id"]]
        seen = set(_MARK.findall(text))
        assert not {m for m in seen if m.startswith("MARK-P")}, (call, seen, _versions())
        if root in ("glob", "-"):
            assert not text.startswith("ERROR:"), (call, text[:300], _versions())
        if root == "glob":
            listed = {line.strip().strip('"') for line in text.replace("\\n", "\n").splitlines()}
            for path in positives:
                assert not any(item.endswith(f"/{path}") or item == path for item in listed), (path, _versions())
            for path in (".kube/configmap.yaml", ".docker/Dockerfile", "agent.py"):
                assert any(item.endswith(path) for item in listed), (path, text[:300], _versions())
            continue
        if root == "-":
            assert not seen, (call, seen)
            continue
        assert seen == _expected(near, root), (call, seen ^ _expected(near, root), _versions())
    kit_seen = set(_MARK.findall(json.dumps(asyncio.run(kit["Grep"]({"pattern": "password"})))))
    scan_seen = set(_MARK.findall(json.dumps(asyncio.run(scan["grep"]({"text": "password"})))))
    assert kit_seen == _expected(near, ""), kit_seen ^ _expected(near, "")
    scan_own = {near[path] for path in SCAN_ONLY}
    assert scan_seen == _expected(near, "") - scan_own, scan_seen ^ (_expected(near, "") - scan_own)

    # The same search inside a worker the session starts with Task keeps to the same rules.
    worker = {"looker": WorkerSpec(description="looks", instructions="look", builtins=("Read", "Glob", "Grep"))}
    with _FakeMessages(parent=parent, worker=[worker_call]) as fake:
        monkeypatch.setenv("ALK_CLAUDE_GATEWAY_URL", fake.url)
        spec = SessionSpec(system_prompt="test", builtins=("Read", "Glob", "Grep"), cwd=str(cwd), model="gemini-test", workers=worker, max_turns=40)
        _run(ClaudeBackend()._options(spec))
    text, role = fake.results()["toolu_worker"]
    assert role == "worker", fake.served
    worker_tools = [set(r["tools"]) for r in fake.requests if r["role"] == "worker"]
    parent_tools = [set(r["tools"]) for r in fake.requests if r["role"] == "parent"]
    assert worker_tools and parent_tools, fake.served
    # The worker's own session: the three file tools it was given, and none of the parent's others.
    assert all({"Read", "Glob", "Grep"} <= tools and len(tools) < len(parent_tools[0]) for tools in worker_tools)
    assert set(_MARK.findall(text)) == _expected(near, ""), _versions()
