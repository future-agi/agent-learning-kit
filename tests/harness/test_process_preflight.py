"""The §2e pre-provision checklist (`process_preflight.py`), per `hosted-execution-seams.md` v1.7.

Every checklist item gets at least one rejection test carrying its named code, plus one clean
accept-lane run of the full checklist. Bundles are built as real directories under `tmp_path` with
real file bytes — the digest and per-file checks need actual content to hash, so a manifest built
purely in memory (as `test_bundle_v2.py` does) cannot exercise this module. No docker; every
managed-engine/process concept here is a manifest fact, never a running service.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from fi.alk.harness.bundle_v2 import (
    BUNDLE_V2_SCHEMA_VERSION,
    EnvironmentBundleV2,
    ManagedEngine,
    compute_inputs_digest,
    seal_bundle_v2,
)
from fi.alk.harness.process_preflight import PreflightError, preflight_bundle

SCHEMA_SQL = b"CREATE TABLE riders (id int);\n"
SEED_SQL = b"INSERT INTO riders VALUES (1);\n"

TARGET_PROVIDER_REFS = {"LIVEKIT_API_KEY": "target_provider"}


def _base_manifest_body() -> dict[str, Any]:
    """A minimal, otherwise-clean `kind: process` manifest: one postgres store behind a real
    seeded capability, one source process that claims the job's only `target_provider` secret."""
    return {
        "schema_version": BUNDLE_V2_SCHEMA_VERSION,
        "name": "demo",
        "runtime": {"kind": "process", "control_service": "agent", "evidence_seam": "http_tool"},
        "processes": [
            {
                "name": "postgres",
                "kind": "managed",
                "engine": "postgres",
                "version": "16",
                "user": "svc-data",
                "depends_on": [],
            },
            {
                "name": "agent",
                "kind": "source",
                "working_directory": ".",
                "build_commands": [["pip", "install", "-r", "requirements.txt"]],
                "run_command": ["python", "agent.py"],
                "environment": {
                    "DATABASE_URL": "{{DATABASE_URL}}",
                    "LIVEKIT_AGENT_NAME": "agent-w{{WORLD_INDEX}}",
                },
                "secret_purposes": ["target_provider"],
                "user": "svc-agent",
                "depends_on": ["postgres"],
            },
        ],
        "capabilities": {
            "database": {
                "protocol": "postgres",
                "service": "postgres",
                "configuration_name": "DATABASE_URL",
            },
        },
        "readiness": [],
        "provenance": {
            "source_kind": "repository", "repository": "org/repo", "source_digest": "c" * 64
        },
        "metadata": {},
    }


def _build_bundle(
    root: Path,
    *,
    body_overrides: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    extra_files: dict[str, bytes] | None = None,
    unlisted_files: dict[str, bytes] | None = None,
    include_seed: bool = True,
) -> EnvironmentBundleV2:
    """Write a real, digest-consistent bundle directory and return its parsed manifest, sealed
    through `seal_bundle_v2` — the module under test's own producer, not a second reimplementation
    of it (the accept lane must prove this module agrees with a real producer, not just with
    itself). ``unlisted_files`` writes real bytes to disk without ever hashing them into
    ``files[]`` — the shape a producer bug or a stray leftover file takes, as opposed to
    ``extra_files``, which is hashed in and fully listed."""
    root.mkdir(parents=True, exist_ok=True)
    body = _base_manifest_body()
    file_contents = {"db/schema.sql": SCHEMA_SQL, "db/seed.sql": SEED_SQL}
    if extra_files:
        file_contents.update(extra_files)

    files: list[dict[str, Any]] = []
    for relative, content in file_contents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append(
            {"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        )
    body["files"] = files

    if include_seed:
        digest = compute_inputs_digest(
            root, ["db/schema.sql"], ["db/seed.sql"], engine=ManagedEngine.POSTGRES, version="16"
        )
        body["seed"] = {
            "stores": [
                {
                    "capability": "database",
                    "migrations": ["db/schema.sql"],
                    "seed_files": ["db/seed.sql"],
                    "baseline": {"strategy": "template_database", "inputs_digest": digest},
                    "sentinel": {"query": "SELECT count(*) FROM riders", "expected": "1"},
                }
            ]
        }

    if body_overrides is not None:
        body = body_overrides(body)

    body["digest"] = "sha256:" + "0" * 64
    normalized = EnvironmentBundleV2.model_validate(body)
    body["digest"] = seal_bundle_v2(normalized)

    if unlisted_files:
        for relative, content in unlisted_files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    (root / "manifest.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    return EnvironmentBundleV2.model_validate(body)


# --- accept lane --------------------------------------------------------------------------------


def test_a_clean_bundle_passes_the_whole_checklist(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    result = preflight_bundle(tmp_path, manifest, parallelism=2, secret_refs=TARGET_PROVIDER_REFS)
    assert result is None


# --- item 1: digest verification ----------------------------------------------------------------


def test_a_file_whose_bytes_changed_after_sealing_is_rejected(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    (tmp_path / "db" / "schema.sql").write_bytes(b"MUTATED")
    with pytest.raises(PreflightError, match="bundle_file_changed") as excinfo:
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)
    assert excinfo.value.code == "bundle_file_changed"


def test_a_files_entry_missing_from_disk_is_rejected(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    (tmp_path / "db" / "schema.sql").unlink()
    with pytest.raises(PreflightError, match="bundle_file_missing"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_bundle_digest_that_does_not_match_the_recomputed_value_is_rejected(
    tmp_path: Path,
) -> None:
    manifest = _build_bundle(tmp_path)
    tampered = manifest.model_copy(update={"digest": "sha256:" + "9" * 64})
    with pytest.raises(PreflightError, match="bundle_digest_mismatch"):
        preflight_bundle(tmp_path, tampered, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


# --- item 2: path safety on the filesystem --------------------------------------------------------


def test_a_symlink_anywhere_under_the_bundle_is_rejected(tmp_path: Path) -> None:
    """The model already forbids unsafe strings in `files[].path`; this is the filesystem-level
    complement — a symlink is rejected even when it sits outside `files[]` entirely."""
    manifest = _build_bundle(tmp_path)
    (tmp_path / "evil-link").symlink_to(tmp_path / "db" / "schema.sql")
    with pytest.raises(PreflightError, match="bundle_symlink_forbidden"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_the_root_manifest_json_itself_being_a_symlink_is_rejected(tmp_path: Path) -> None:
    """N3 (p4-round2-review): the root `manifest.json` is exempt from the listing check (a
    manifest cannot list itself), but that exemption must not extend to its symlink status — a
    symlinked root manifest was previously read straight through by `_verify_digest` and
    `_verify_unknown_fields`, the very item whose job is to stop path escapes."""
    manifest = _build_bundle(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-manifest-target.json"
    outside.write_bytes((tmp_path / "manifest.json").read_bytes())
    (tmp_path / "manifest.json").unlink()
    (tmp_path / "manifest.json").symlink_to(outside)
    with pytest.raises(PreflightError, match="bundle_symlink_forbidden"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_file_present_on_disk_but_not_listed_in_files_is_rejected(tmp_path: Path) -> None:
    """F1 (p4-round1-review): a bundle directory containing a file the producer never hashed into
    `files[]` was invisible to both the digest check and the secret scan — this is the case that
    previously slipped a `.env` through undetected. `extra_files` (used by the item-3 tests below)
    would have hashed it in; `unlisted_files` writes the same bytes without listing them at all."""
    manifest = _build_bundle(tmp_path, unlisted_files={".env": b"SECRET=1\n"})
    with pytest.raises(PreflightError, match="bundle_file_unlisted"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


# --- item 3: secret material in the bundle's own files -------------------------------------------


def test_a_dotenv_file_in_the_bundle_is_rejected(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path, extra_files={".env": b"SECRET=1\n"})
    with pytest.raises(PreflightError, match="secret_in_bundle"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_high_entropy_secret_content_in_a_bundle_file_is_rejected(tmp_path: Path) -> None:
    manifest = _build_bundle(
        tmp_path, extra_files={"db/notes.sql": b"-----BEGIN RSA PRIVATE KEY-----\nabc\n"}
    )
    with pytest.raises(PreflightError, match="secret_in_bundle"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


# --- item 4: unknown-field translation ------------------------------------------------------------


def test_an_unknown_field_added_to_the_manifest_on_disk_is_rejected(tmp_path: Path) -> None:
    """The `manifest` argument is already-parsed and therefore already clean; this proves the
    translation fires against the bytes on disk, which is what a drifted or hand-edited
    `manifest.json` would look like to a fresh `EnvironmentBundleV2.model_validate` call."""
    manifest = _build_bundle(tmp_path)
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    raw["mounts"] = ["/data"]
    (tmp_path / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PreflightError, match="unknown_field") as excinfo:
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)
    assert excinfo.value.code == "unknown_field"


def test_a_valid_but_drifted_manifest_on_disk_is_rejected(tmp_path: Path) -> None:
    """F12 (p4-round1-review): re-validating the bytes on disk only catches drift that makes the
    file *invalid* — this proves drift that leaves it valid (a changed `run_command`) is caught
    too, by actually comparing the two dumps rather than discarding the re-validated one."""
    manifest = _build_bundle(tmp_path)
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    raw["processes"][1]["run_command"] = ["python", "other.py"]
    (tmp_path / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PreflightError, match="bundle_manifest_drifted") as excinfo:
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)
    assert excinfo.value.code == "bundle_manifest_drifted"


def test_an_unreadable_manifest_json_on_disk_is_rejected(tmp_path: Path) -> None:
    """F18 (p4-round1-review): `bundle_manifest_invalid`'s parse-failure branch, untested before."""
    manifest = _build_bundle(tmp_path)
    (tmp_path / "manifest.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(PreflightError, match="bundle_manifest_invalid"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_non_extra_forbidden_model_rejection_surfaces_its_own_code(tmp_path: Path) -> None:
    """F13/F18 (p4-round1-review): `_translate_validation_error`'s fallback path was untested. A
    malformed on-disk `digest` (item 1 never reads it — only the `manifest` argument's own valid
    digest and file hashes, which are untouched here) reaches item 4's re-validation and raises
    `bundle_digest_invalid`, a bare code with no trailing colon at all — exactly the case the old
    `:`-only regex flattened to the generic `bundle_manifest_invalid`."""
    manifest = _build_bundle(tmp_path)
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    raw["digest"] = "not-a-valid-digest"
    (tmp_path / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PreflightError, match="bundle_digest_invalid") as excinfo:
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)
    assert excinfo.value.code == "bundle_digest_invalid"


# --- §2a: compose is not a legal hosted runtime kind ----------------------------------------------


def test_kind_compose_is_rejected(tmp_path: Path) -> None:
    """N1 (p4-round2-review): the gate sits above item 1, so this fires regardless of whether
    `compose.yaml` exists on disk or is listed in `files[]` — a compose bundle need carry neither
    to be rejected, which is why this fixture writes no such file at all."""
    manifest = _build_bundle(
        tmp_path,
        body_overrides=lambda b: {
            **b,
            "runtime": {"kind": "compose", "document": "compose.yaml"},
            "processes": [],
            "seed": None,
        },
        include_seed=False,
    )
    with pytest.raises(PreflightError, match="compose_not_hosted"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs={})


# --- item 5: placeholder vocabulary, secret purposes, depends_on, engine catalog, interpreter,
# seed_missing, reserved names, build_requires_root, seed files on disk+listed ---------------------


def test_a_placeholder_outside_the_closed_vocabulary_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["environment"]["FOO"] = "{{NOT_A_REAL_TOKEN}}"
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="unknown_placeholder"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_port_placeholder_naming_an_unknown_process_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["environment"]["PEER_PORT"] = "{{PORT_ghost-service}}"
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="unknown_placeholder"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_port_placeholder_with_an_empty_name_falls_through_to_unknown_placeholder(
    tmp_path: Path,
) -> None:
    """F18 (p4-round1-review): `(.+)` in `_NAMED_PLACEHOLDER` requires at least one character after
    `PORT_`/`HOST_`, so `{{PORT_}}` does not match the named-placeholder pattern at all — it falls
    through to the configuration-name set, misses, and is rejected as `unknown_placeholder`. A
    plausible regression target if `(.+)` is ever relaxed to `(.*)`."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["environment"]["EMPTY"] = "{{PORT_}}"
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="unknown_placeholder"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_token_naming_a_capability_with_no_configuration_name_is_capability_unresolved(
    tmp_path: Path,
) -> None:
    """F15 (p4-round1-review): a null `configuration_name` is structurally unspellable by any
    placeholder — but when the unmatched token happens to be exactly a declared capability's own
    slug, the real problem is the capability's missing name, not an unrecognized token, so this is
    reported `capability_unresolved` naming the capability rather than the generic
    `unknown_placeholder`."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["capabilities"]["cache"] = {"protocol": "http", "service": "postgres"}
        body["processes"][1]["environment"]["FOO"] = "{{cache}}"
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="capability_unresolved"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_build_environment_rejects_any_placeholder_even_a_legal_one(tmp_path: Path) -> None:
    """F6 (p4-round1-review): §2b's `build_environment` takes NO placeholders at all — this uses
    `{{WORLD_DIR}}`, a perfectly legal token in `environment`, specifically because that is the
    case a naive "scan against the same vocabulary" implementation would wave through."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["build_environment"] = {"TMPDIR": "{{WORLD_DIR}}"}
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="unknown_placeholder"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_the_fixed_and_named_placeholders_are_accepted(tmp_path: Path) -> None:
    """`{{WORLD_INDEX}}`/`{{WORLD_DIR}}`/`{{DB_NAME}}` need no lookup; `{{PORT_<name>}}` and
    `{{HOST_<name>}}` need only a real process name — neither needs a capability."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["environment"]["SCRATCH"] = "{{WORLD_DIR}}"
        body["processes"][1]["environment"]["DB"] = "{{DB_NAME}}"
        body["processes"][1]["environment"]["PEER"] = "{{HOST_postgres}}:{{PORT_postgres}}"
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    result = preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)
    assert result is None


def test_a_build_command_requiring_root_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["build_commands"] = [["apt-get", "install", "-y", "ffmpeg"]]
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="build_requires_root"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_build_command_with_sudo_anywhere_in_the_step_is_rejected(tmp_path: Path) -> None:
    """F18 (p4-round1-review): only `apt-get` as argv[0] was exercised before — `"sudo" in step`
    is exact-token list membership, not a substring match, so `sudo` appearing anywhere in the
    argv list (not just as argv[0]) must trip it too."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["build_commands"] = [["scripts/setup.sh", "--with-sudo", "sudo"]]
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="build_requires_root"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_target_provider_ref_no_process_lists_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["secret_purposes"] = []
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="secret_unclaimed"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_listed_secret_purpose_with_no_supplying_ref_is_rejected(tmp_path: Path) -> None:
    manifest = _build_bundle(tmp_path)
    with pytest.raises(PreflightError, match="secret_missing"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs={})


def test_omitting_secret_refs_is_a_typeerror(tmp_path: Path) -> None:
    """F2 (p4-round1-review): `secret_refs` is required, not optional with an empty-dict default —
    an optional default meant `secret_unclaimed` was structurally unreachable for any caller that
    relied on it, while `secret_missing` fired on bundles the default should have let alone. This
    pins the signature itself: omitting the argument must fail loudly, at the call site, rather
    than silently reintroducing the empty-dict default."""
    manifest = _build_bundle(tmp_path)
    with pytest.raises(TypeError):
        preflight_bundle(tmp_path, manifest, parallelism=1)  # type: ignore[call-arg]


def test_an_unrecognized_secret_purpose_value_is_rejected(tmp_path: Path) -> None:
    """F2 (p4-round1-review), the signature's second defect: `secret_refs` values are only ever
    compared by `==` against `SecretPurpose.TARGET_PROVIDER.value` — a typo'd purpose string (or
    §1's raw per-alias dict shape, whose `purpose` values are dicts, not strings) silently never
    matches, producing the same wrong verdict as the missing-validation default did. Validating
    eagerly turns that into a loud, immediate `ValueError`."""
    manifest = _build_bundle(tmp_path)
    with pytest.raises(ValueError, match="not a SecretPurpose"):
        preflight_bundle(
            tmp_path, manifest, parallelism=1, secret_refs={"LIVEKIT_API_KEY": "target-provider"}
        )


def test_a_depends_on_naming_an_unknown_process_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["depends_on"] = ["postgres", "ghost"]
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="depends_on_unresolved"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_depends_on_cycle_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][0]["depends_on"] = ["agent"]
        body["processes"][1]["depends_on"] = ["postgres"]
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="depends_on_cycle"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_an_engine_version_outside_the_catalog_pin_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][0]["version"] = "15"
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="engine_unsupported"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_postgres_capability_with_no_store_entry_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["capabilities"]["other_db"] = {
            "protocol": "postgres",
            "service": "postgres",
            "configuration_name": "OTHER_DB_URL",
        }
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="seed_missing"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def _reseal_schema_sql(tmp_path: Path, content: bytes) -> EnvironmentBundleV2:
    """Rewrites `db/schema.sql` on disk and reseals through `seal_bundle_v2` — so digest
    verification (item 1, which runs before item 5's content-scanning checks) passes on the
    mutated content, isolating a test to the check the mutation is actually meant to exercise."""
    (tmp_path / "db" / "schema.sql").write_bytes(content)
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    for record in raw["files"]:
        if record["path"] == "db/schema.sql":
            record["sha256"] = hashlib.sha256(content).hexdigest()
            record["size"] = len(content)
    raw["seed"]["stores"][0]["baseline"]["inputs_digest"] = compute_inputs_digest(
        tmp_path, ["db/schema.sql"], ["db/seed.sql"], engine=ManagedEngine.POSTGRES, version="16"
    )
    raw["digest"] = "sha256:" + "0" * 64
    normalized = EnvironmentBundleV2.model_validate(raw)
    raw["digest"] = seal_bundle_v2(normalized)
    (tmp_path / "manifest.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return EnvironmentBundleV2.model_validate(raw)


def test_the_reserved_conformance_name_in_migration_content_is_rejected(tmp_path: Path) -> None:
    _build_bundle(tmp_path)
    manifest = _reseal_schema_sql(tmp_path, b"CREATE TABLE _alk_conformance (id int);\n")
    with pytest.raises(PreflightError, match="reserved_name"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_the_reserved_conformance_name_is_matched_case_insensitively(tmp_path: Path) -> None:
    """F9 (p4-round1-review): postgres folds an unquoted identifier to lower case, so
    `CREATE TABLE _ALK_CONFORMANCE` creates the reserved table under its lower-case name — a
    case-sensitive scan would miss exactly the evasion this exists to catch."""
    _build_bundle(tmp_path)
    manifest = _reseal_schema_sql(tmp_path, b"CREATE TABLE _ALK_CONFORMANCE (id int);\n")
    with pytest.raises(PreflightError, match="reserved_name"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_similarly_named_table_and_a_comment_mentioning_the_reserved_name_are_accepted(
    tmp_path: Path,
) -> None:
    """F9 (p4-round1-review) regression: `alk_conformance_backup` (no leading underscore, and a
    different table) must not trip the scan — this is exactly what the lookarounds exist to keep
    open. A `--` comment mentioning the reserved name as prose, not as an identifier it defines,
    must not trip it either, now that comments are stripped before scanning."""
    _build_bundle(tmp_path)
    manifest = _reseal_schema_sql(
        tmp_path,
        b"CREATE TABLE alk_conformance_backup (id int);\n"
        b"-- never create _alk_conformance here\n",
    )
    result = preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)
    assert result is None


def test_a_migration_path_not_listed_in_files_is_rejected(tmp_path: Path) -> None:
    """`seed_file_unlisted` (item 5) and `bundle_file_unlisted` (item 2, F1 p4-round1-review) share
    the same "on disk but not in files[]" condition — item 2's bundle-wide walk now runs first and
    always wins this exact scenario, which is why this asserts the earlier-numbered item's code
    rather than the one this test used to name before F1 closed item 2's gap."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["seed"]["stores"][0]["migrations"] = ["db/schema.sql", "db/extra.sql"]
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    # On disk, but never hashed into files[].
    (tmp_path / "db" / "extra.sql").write_bytes(b"-- extra\n")
    with pytest.raises(PreflightError, match="bundle_file_unlisted"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_seed_file_path_that_does_not_exist_on_disk_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["seed"]["stores"][0]["seed_files"] = ["db/seed.sql", "db/ghost.sql"]
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="seed_file_missing"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_recorded_inputs_digest_that_does_not_match_the_seed_files_is_rejected(
    tmp_path: Path,
) -> None:
    """F14 (p4-round1-review): §2c makes `inputs_digest` the baseline identity attempt-retry reuse
    trusts absolutely — nothing on either side of the seam validated it before. `engine`/`version`
    come from the store's capability's own backing `ManagedProcess` (postgres/16 here)."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["seed"]["stores"][0]["baseline"]["inputs_digest"] = "sha256:" + "f" * 64
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="inputs_digest_mismatch"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


# --- item 6: no_sql_store ------------------------------------------------------------------------


def test_a_process_bundle_with_no_postgres_capability_is_rejected(tmp_path: Path) -> None:
    """Keeps the `database` capability (so the `{{DATABASE_URL}}` placeholder in `agent`'s
    environment still resolves) and only changes its protocol away from postgres, isolating this
    from the placeholder-vocabulary check that would otherwise fire first."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["capabilities"]["database"]["protocol"] = "http"
        body["seed"] = None
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate, include_seed=False)
    with pytest.raises(PreflightError, match="no_sql_store"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


# --- item 7: resource sanity ---------------------------------------------------------------------


def test_more_than_100_processes_is_rejected(tmp_path: Path) -> None:
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        extra = [
            {
                "name": f"extra-{i}",
                "kind": "managed",
                "engine": "redis",
                "version": "7",
                "user": "svc-data",
                "depends_on": [],
            }
            for i in range(100)
        ]
        body["processes"] = body["processes"] + extra
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="process_count_exceeded"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


@pytest.mark.parametrize("parallelism", [0, 9], ids=["below-range", "above-range"])
def test_parallelism_outside_1_to_8_is_rejected(tmp_path: Path, parallelism: int) -> None:
    manifest = _build_bundle(tmp_path)
    with pytest.raises(PreflightError, match="parallelism_out_of_range"):
        preflight_bundle(
            tmp_path, manifest, parallelism=parallelism, secret_refs=TARGET_PROVIDER_REFS
        )


# --- ordering: the checklist runs in the contract's numbered order, and a bundle violating two
# rules at once must report the earlier-numbered one (F17, p4-round1-review) — every rejection
# fixture above proves presence, none of them alone proves position. --------------------------


def test_a_secret_file_and_an_unknown_placeholder_together_report_the_earlier_numbered_item(
    tmp_path: Path,
) -> None:
    """Item 3 (secret scan) before item 5 (placeholder vocabulary)."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["environment"]["FOO"] = "{{NOT_A_REAL_TOKEN}}"
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate, extra_files={".env": b"SECRET=1\n"})
    with pytest.raises(PreflightError, match="secret_in_bundle"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_changed_file_and_a_reserved_name_together_report_the_earlier_numbered_item(
    tmp_path: Path,
) -> None:
    """Item 1 (digest verification) before item 5's reserved-name scan — the mutated file carries
    both a byte-level tamper the digest catches and a reserved name the content scan would catch,
    but the manifest is never resealed, so item 1 sees it first."""
    manifest = _build_bundle(tmp_path)
    (tmp_path / "db" / "schema.sql").write_bytes(b"CREATE TABLE _alk_conformance (id int);\n")
    with pytest.raises(PreflightError, match="bundle_file_changed"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs=TARGET_PROVIDER_REFS)


def test_a_placeholder_and_bad_parallelism_together_report_the_earlier_numbered_item(
    tmp_path: Path,
) -> None:
    """Item 5 (placeholder vocabulary) before item 7 (resource sanity)."""
    def mutate(body: dict[str, Any]) -> dict[str, Any]:
        body["processes"][1]["environment"]["FOO"] = "{{NOT_A_REAL_TOKEN}}"
        return body

    manifest = _build_bundle(tmp_path, body_overrides=mutate)
    with pytest.raises(PreflightError, match="unknown_placeholder"):
        preflight_bundle(tmp_path, manifest, parallelism=99, secret_refs=TARGET_PROVIDER_REFS)


def test_a_compose_bundle_with_an_unlisted_file_reports_compose_not_hosted(
    tmp_path: Path,
) -> None:
    """N1 (p4-round2-review): the compose gate before item 1, before item 2 (`test_kind_compose_
    is_rejected` alone cannot prove position, since it now carries no file to trip item 2 at all).
    An unlisted `compose.yaml` would report `bundle_file_unlisted` if item 2 ran first — this pins
    that the gate wins instead."""
    manifest = _build_bundle(
        tmp_path,
        body_overrides=lambda b: {
            **b,
            "runtime": {"kind": "compose", "document": "compose.yaml"},
            "processes": [],
            "seed": None,
        },
        include_seed=False,
    )
    (tmp_path / "compose.yaml").write_bytes(b"services: {}\n")
    with pytest.raises(PreflightError, match="compose_not_hosted"):
        preflight_bundle(tmp_path, manifest, parallelism=1, secret_refs={})


# --- kind: external ------------------------------------------------------------------------------


def test_kind_external_skips_the_process_block_but_still_enforces_resource_sanity(
    tmp_path: Path,
) -> None:
    """F18 (p4-round1-review): the only prior `external` coverage was model-layer
    (`test_bundle_v2.py`) — nothing proved that `preflight_bundle` itself skips item 5's process
    block and item 6 (`no_sql_store`) for `kind: external`, and still applies item 7."""
    manifest = _build_bundle(
        tmp_path,
        body_overrides=lambda b: {
            **b,
            "runtime": {"kind": "external"},
            "processes": [],
            "seed": None,
            "capabilities": {
                "target": {
                    "protocol": "http",
                    "service": "customer-endpoint",
                    "configuration_name": "TARGET_URL",
                },
            },
        },
        include_seed=False,
    )
    # Item 5 (placeholder/secret-purpose/depends_on/engine-catalog/seed checks) and item 6
    # (no_sql_store) never run — nothing in this manifest could satisfy either, since it has no
    # processes at all.
    assert preflight_bundle(tmp_path, manifest, parallelism=2, secret_refs={}) is None
    # Item 7 still runs regardless of runtime kind.
    with pytest.raises(PreflightError, match="parallelism_out_of_range"):
        preflight_bundle(tmp_path, manifest, parallelism=99, secret_refs={})
