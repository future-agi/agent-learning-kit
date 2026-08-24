"""`futureagi.environment-bundle.v2` model validation, per `hosted-execution-seams.md` v1.6 §2.

Two lanes: the spec's own §2a/§2b/§2c example structures, transcribed here and proven to parse
(the model-layer accept side), against the rejections the model is responsible for on its own —
wrong schema version, an unknown process kind, a store with no sentinel, a sentinel shape that
disagrees with its capability's protocol, a strategy the capability's protocol-implied engine does
not support, unresolved `service`/`control_service`/capability references, a duplicate process
name, and a resolved secret value anywhere in the manifest. `compute_inputs_digest` is checked
against a hand-computed vector, not by calling back into itself.

Rules that need a repo checkout, the job the bundle will run under, or the §2e checklist
(`compose_not_hosted`, `engine_unsupported`, `no_sql_store`, `depends_on` cycles, placeholder
vocabulary, reserved-name scanning) belong to Phase 4's preflight and are out of scope here.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from fi.alk.harness.bundle_v2 import (
    BUNDLE_V2_SCHEMA_VERSION,
    BundleRuntimeV2,
    BundleV2Error,
    EnvironmentBundleV2,
    ManagedEngine,
    ManagedProcess,
    SourceProcess,
    StoreEntry,
    compute_inputs_digest,
    load_bundle_v2,
)

# --- §2a/§2b/§2c: the spec's own examples, transcribed verbatim -----------------------------

RUNTIME_EXAMPLE = {"kind": "process", "control_service": "agent", "evidence_seam": "http_tool"}

POSTGRES_PROCESS_EXAMPLE = {
    "name": "postgres",
    "kind": "managed",
    "engine": "postgres",
    "version": "16",
    "user": "svc-data",
    "depends_on": [],
}

TOOLS_API_PROCESS_EXAMPLE = {
    "name": "tools-api",
    "kind": "source",
    "working_directory": "services/tools-api",
    "build_commands": [["npm", "ci"]],
    "run_command": ["node", "server.js"],
    "environment": {
        "DATABASE_URL": "{{DATABASE_URL}}",
        "PORT": "{{PORT_tools-api}}",
        "TMPDIR": "{{WORLD_DIR}}",
    },
    "secret_purposes": [],
    "user": "svc-tools",
    "depends_on": ["postgres"],
}

AGENT_PROCESS_EXAMPLE = {
    "name": "agent",
    "kind": "source",
    "working_directory": ".",
    "build_commands": [["pip", "install", "-r", "requirements.txt"]],
    "run_command": ["python", "agent/agent.py", "start"],
    "environment": {
        "DATABASE_URL": "{{DATABASE_URL}}",
        "TOOLS_API_URL": "{{TOOLS_API_URL}}",
        "LIVEKIT_AGENT_NAME": "agent-w{{WORLD_INDEX}}",
    },
    "secret_purposes": ["target_provider"],
    "user": "svc-agent",
    "depends_on": ["postgres", "tools-api"],
}

# §2b catalog-table engines beyond postgres, for the capability-protocol/sentinel/strategy pairing
# tests (F1) — a store's engine now comes from its capability's protocol, so these tests need a
# real managed process of the matching engine for the capability's `service` to resolve to.
REDIS_PROCESS_EXAMPLE = {
    "name": "cache",
    "kind": "managed",
    "engine": "redis",
    "version": "7",
    "user": "svc-data",
    "depends_on": [],
}

RABBITMQ_PROCESS_EXAMPLE = {
    "name": "queue",
    "kind": "managed",
    "engine": "rabbitmq",
    "version": "3.13",
    "user": "svc-data",
    "depends_on": [],
}

# The spec's own `<64-hex>` placeholder, filled with a real hex string — the example is only
# illustrating the digest's shape, not a value to reproduce.
SEED_STORE_EXAMPLE = {
    "capability": "database",
    "migrations": ["db/schema.sql"],
    "seed_files": ["db/seed.sql"],
    "baseline": {
        "strategy": "template_database",
        "inputs_digest": "sha256:" + "a" * 64,
    },
    "sentinel": {"query": "SELECT count(*) FROM riders", "expected": "12"},
}

FULL_MANIFEST_EXAMPLE = {
    "schema_version": BUNDLE_V2_SCHEMA_VERSION,
    "digest": "sha256:" + "0" * 64,
    "name": "demo",
    "runtime": RUNTIME_EXAMPLE,
    "processes": [POSTGRES_PROCESS_EXAMPLE, TOOLS_API_PROCESS_EXAMPLE, AGENT_PROCESS_EXAMPLE],
    "seed": {"stores": [SEED_STORE_EXAMPLE]},
    "capabilities": {
        "database": {
            "protocol": "postgres",
            "service": "postgres",
            "configuration_name": "DATABASE_URL",
        },
        "tools": {
            "protocol": "http",
            "service": "tools-api",
            "configuration_name": "TOOLS_API_URL",
        },
    },
    "readiness": [{"capability": "tools", "path": "/healthz"}],
    "files": [{"path": "db/schema.sql", "sha256": "b" * 64, "size": 10}],
    "provenance": {
        "source_kind": "repository",
        "repository": "org/repo",
        "commit": "a" * 40,
        # Bare 64-hex — what v1's `source_fingerprint` producer actually emits, unlike the
        # `sha256:`-prefixed `digest`/`inputs_digest`.
        "source_digest": "c" * 64,
    },
}


def test_the_runtime_example_from_2a_is_accepted() -> None:
    runtime = BundleRuntimeV2.model_validate(RUNTIME_EXAMPLE)
    assert runtime.kind.value == "process"
    assert runtime.evidence_seam.value == "http_tool"


def test_the_managed_process_example_from_2b_is_accepted() -> None:
    process = ManagedProcess.model_validate(POSTGRES_PROCESS_EXAMPLE)
    assert process.engine.value == "postgres"
    assert process.version == "16"


@pytest.mark.parametrize(
    "example", [TOOLS_API_PROCESS_EXAMPLE, AGENT_PROCESS_EXAMPLE], ids=["tools-api", "agent"]
)
def test_the_source_process_examples_from_2b_are_accepted(example: dict) -> None:
    process = SourceProcess.model_validate(example)
    assert process.run_command
    assert process.working_directory == example["working_directory"]


def test_the_seed_store_example_from_2c_is_accepted() -> None:
    store = StoreEntry.model_validate(SEED_STORE_EXAMPLE)
    assert store.baseline.strategy.value == "template_database"
    assert store.sentinel.implied_engine is ManagedEngine.POSTGRES


def test_the_full_manifest_assembled_from_the_spec_examples_is_accepted() -> None:
    bundle = EnvironmentBundleV2.model_validate(FULL_MANIFEST_EXAMPLE)
    assert bundle.name == "demo"
    assert [process.name for process in bundle.processes] == ["postgres", "tools-api", "agent"]
    assert bundle.seed is not None and bundle.seed.stores[0].capability == "database"


# --- rejections the model is responsible for on its own -------------------------------------


def test_wrong_schema_version_is_rejected() -> None:
    manifest = {**FULL_MANIFEST_EXAMPLE, "schema_version": "futureagi.environment-bundle.v1"}
    with pytest.raises(ValidationError, match="bundle_schema_unsupported"):
        EnvironmentBundleV2.model_validate(manifest)


def test_unknown_process_kind_is_rejected() -> None:
    manifest = {
        **FULL_MANIFEST_EXAMPLE,
        "processes": [{**POSTGRES_PROCESS_EXAMPLE, "kind": "container"}],
    }
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        EnvironmentBundleV2.model_validate(manifest)


def test_a_store_with_no_sentinel_is_rejected() -> None:
    incomplete = {key: value for key, value in SEED_STORE_EXAMPLE.items() if key != "sentinel"}
    with pytest.raises(ValidationError, match="sentinel"):
        StoreEntry.model_validate(incomplete)


# The engine a store answers to is now the engine behind its *capability's protocol* (F1), decided
# in the root validator — so these cases are built as full manifests, not bare `StoreEntry`s: the
# capability, its protocol, and a real process for `service` to resolve to (F5) all have to be in
# scope together for the rule to fire at all.
@pytest.mark.parametrize(
    "process,protocol,sentinel,strategy",
    [
        # redis's sentinel shape only pairs with datadir_copy or empty (§2b's catalog table).
        (REDIS_PROCESS_EXAMPLE, "redis", {"key": "warm", "expected": "1"}, "template_database"),
        # rabbitmq's sentinel shape only pairs with datadir_copy.
        (RABBITMQ_PROCESS_EXAMPLE, "amqp", {"queue": "jobs", "expected_depth": 0}, "empty"),
    ],
    ids=["redis", "rabbitmq"],
)
def test_a_strategy_the_capabilitys_engine_does_not_support_is_rejected(
    process: dict, protocol: str, sentinel: dict, strategy: str
) -> None:
    manifest = {
        **FULL_MANIFEST_EXAMPLE,
        "processes": [*FULL_MANIFEST_EXAMPLE["processes"], process],
        "capabilities": {
            **FULL_MANIFEST_EXAMPLE["capabilities"],
            "store": {
                "protocol": protocol,
                "service": process["name"],
                "configuration_name": "STORE_URL",
            },
        },
        "seed": {
            "stores": [
                SEED_STORE_EXAMPLE,
                {
                    "capability": "store",
                    "baseline": {"strategy": strategy, "inputs_digest": "sha256:" + "a" * 64},
                    "sentinel": sentinel,
                },
            ]
        },
    }
    with pytest.raises(ValidationError, match="seed_strategy_unsupported"):
        EnvironmentBundleV2.model_validate(manifest)


def test_a_redis_capability_with_a_postgres_shaped_sentinel_is_rejected() -> None:
    """The capability's protocol decides the engine, not the sentinel's own shape — a redis
    capability paired with a postgres-shaped sentinel is a shape mismatch even though the sentinel
    is internally well-formed and the strategy is one postgres would have accepted."""
    manifest = {
        **FULL_MANIFEST_EXAMPLE,
        "capabilities": {
            **FULL_MANIFEST_EXAMPLE["capabilities"],
            "database": {**FULL_MANIFEST_EXAMPLE["capabilities"]["database"], "protocol": "redis"},
        },
    }
    with pytest.raises(ValidationError, match="sentinel_shape_mismatch"):
        EnvironmentBundleV2.model_validate(manifest)


def test_a_postgres_capability_with_a_typod_redis_shaped_sentinel_is_rejected() -> None:
    """A postgres capability whose sentinel was typo'd to redis's `{key, expected}` shape is
    rejected naming the sentinel mismatch, not `seed_strategy_unsupported` against an engine
    (`redis`) the bundle never declared — the bug this replaces would have named the wrong one."""
    manifest = {
        **FULL_MANIFEST_EXAMPLE,
        "seed": {"stores": [{**SEED_STORE_EXAMPLE, "sentinel": {"key": "warm", "expected": "1"}}]},
    }
    with pytest.raises(ValidationError, match="sentinel_shape_mismatch") as exc_info:
        EnvironmentBundleV2.model_validate(manifest)
    assert "seed_strategy_unsupported" not in str(exc_info.value)


def test_a_sentinel_mixing_two_protocol_shapes_is_rejected() -> None:
    store = {
        "capability": "database",
        "baseline": {"strategy": "empty", "inputs_digest": "sha256:" + "a" * 64},
        "sentinel": {"query": "SELECT 1", "expected": "1", "key": "also-set"},
    }
    with pytest.raises(ValidationError, match="sentinel_shape_invalid"):
        StoreEntry.model_validate(store)


def test_unknown_field_on_a_process_entry_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ManagedProcess.model_validate({**POSTGRES_PROCESS_EXAMPLE, "mounts": ["/data"]})


# --- §2a/§2b/§2d rules the model owns on its own, exercised at manifest scope ----------------


def test_a_process_runtime_without_evidence_seam_is_rejected() -> None:
    manifest = {**FULL_MANIFEST_EXAMPLE, "runtime": {"kind": "process", "control_service": "agent"}}
    with pytest.raises(ValidationError, match="evidence_seam_required"):
        EnvironmentBundleV2.model_validate(manifest)


def test_a_process_runtime_with_no_processes_is_rejected() -> None:
    manifest = {**FULL_MANIFEST_EXAMPLE, "processes": []}
    with pytest.raises(ValidationError, match="processes_required"):
        EnvironmentBundleV2.model_validate(manifest)


def test_an_external_runtime_carrying_processes_or_seed_is_rejected() -> None:
    manifest = {**FULL_MANIFEST_EXAMPLE, "runtime": {"kind": "external"}}
    with pytest.raises(ValidationError, match="processes_and_seed_forbidden"):
        EnvironmentBundleV2.model_validate(manifest)


def test_a_readiness_probe_naming_an_unresolved_capability_is_rejected() -> None:
    manifest = {**FULL_MANIFEST_EXAMPLE, "readiness": [{"capability": "does-not-exist"}]}
    with pytest.raises(ValidationError, match="capability_unresolved"):
        EnvironmentBundleV2.model_validate(manifest)


def test_a_duplicate_process_name_is_rejected() -> None:
    manifest = {
        **FULL_MANIFEST_EXAMPLE,
        "processes": [*FULL_MANIFEST_EXAMPLE["processes"], dict(POSTGRES_PROCESS_EXAMPLE)],
    }
    with pytest.raises(ValidationError, match="process_name_duplicate"):
        EnvironmentBundleV2.model_validate(manifest)


def test_a_capability_service_naming_an_unknown_process_is_rejected() -> None:
    """The underscore/hyphen slip finding 5 names directly: a `service` of `tools_api` against a
    process actually named `tools-api` must be caught here, not surface as an infrastructure
    failure when the provisioner finds nothing to attach the capability to."""
    manifest = {
        **FULL_MANIFEST_EXAMPLE,
        "capabilities": {
            **FULL_MANIFEST_EXAMPLE["capabilities"],
            "tools": {**FULL_MANIFEST_EXAMPLE["capabilities"]["tools"], "service": "tools_api"},
        },
    }
    with pytest.raises(ValidationError, match="service_unresolved"):
        EnvironmentBundleV2.model_validate(manifest)


def test_an_unresolved_control_service_is_rejected() -> None:
    manifest = {
        **FULL_MANIFEST_EXAMPLE,
        "runtime": {**RUNTIME_EXAMPLE, "control_service": "not-a-process"},
    }
    with pytest.raises(ValidationError, match="control_service_unresolved"):
        EnvironmentBundleV2.model_validate(manifest)


def test_a_resolved_secret_value_in_process_environment_is_rejected() -> None:
    """v1's manifest-level guard, reapplied: `environment`/`build_environment` are new in v2 and
    are exactly where a resolved credential lands if an authoring stage inlines one instead of
    routing it through `secret_purposes`."""
    manifest = {
        **FULL_MANIFEST_EXAMPLE,
        "processes": [
            POSTGRES_PROCESS_EXAMPLE,
            TOOLS_API_PROCESS_EXAMPLE,
            {
                **AGENT_PROCESS_EXAMPLE,
                "environment": {
                    **AGENT_PROCESS_EXAMPLE["environment"],
                    "STRIPE_API_KEY": "sk_live_x",
                },
            },
        ],
    }
    with pytest.raises(ValidationError, match="resolved_secret_forbidden"):
        EnvironmentBundleV2.model_validate(manifest)


# --- §2c inputs_digest: byte-exact construction, checked against a hand-computed vector -----


def test_compute_inputs_digest_matches_a_hand_computed_vector(tmp_path) -> None:
    (tmp_path / "db").mkdir()
    (tmp_path / "café").mkdir()
    schema = b"CREATE TABLE riders (id int);"
    # Multi-byte content pins content_length as a byte count, not a character count — reading via
    # read_text() + len(text) would compute 8 here instead of 9 and still hash to something, just
    # not this. Path with a non-ASCII segment pins the path encoding as utf-8, not latin-1.
    seed = "-- café\n".encode("utf-8")
    notes = b"-- see docs"
    (tmp_path / "db" / "schema.sql").write_bytes(schema)
    (tmp_path / "db" / "seed.sql").write_bytes(seed)
    (tmp_path / "café" / "notes.sql").write_bytes(notes)

    # Built from §2c's own words, not by calling the helper: for each file in migrations then
    # seed_files, in listed order, <relative_path>\n<content_length>\n<content_bytes>, then
    # <engine>:<version>\n.
    expected = hashlib.sha256()
    expected.update(b"db/schema.sql\n" + str(len(schema)).encode() + b"\n" + schema)
    expected.update(b"db/seed.sql\n" + str(len(seed)).encode() + b"\n" + seed)
    notes_path = "café/notes.sql".encode("utf-8")
    expected.update(notes_path + b"\n" + str(len(notes)).encode() + b"\n" + notes)
    expected.update(b"postgres:16\n")

    got = compute_inputs_digest(
        tmp_path,
        ["db/schema.sql"],
        ["db/seed.sql", "café/notes.sql"],
        engine=ManagedEngine.POSTGRES,
        version="16",
    )
    assert got == "sha256:" + expected.hexdigest()


def test_compute_inputs_digest_is_order_sensitive_not_sorted(tmp_path) -> None:
    """Two migrations reversed must hash differently — order is part of the identity, since
    migrations apply in sequence, not in whatever order sorting would put them in."""
    (tmp_path / "a.sql").write_bytes(b"A")
    (tmp_path / "b.sql").write_bytes(b"B")

    forward = compute_inputs_digest(
        tmp_path, ["a.sql", "b.sql"], [], engine=ManagedEngine.POSTGRES, version="16"
    )
    backward = compute_inputs_digest(
        tmp_path, ["b.sql", "a.sql"], [], engine=ManagedEngine.POSTGRES, version="16"
    )
    assert forward != backward


# --- load_bundle_v2 --------------------------------------------------------------------------


def test_load_bundle_v2_rejects_a_v1_manifest_with_a_typed_error(tmp_path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": "futureagi.environment-bundle.v1"})
    )
    with pytest.raises(BundleV2Error, match="bundle_schema_unsupported"):
        load_bundle_v2(tmp_path)


def test_load_bundle_v2_parses_a_valid_manifest_from_its_directory(tmp_path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps(FULL_MANIFEST_EXAMPLE))
    bundle = load_bundle_v2(tmp_path)
    assert bundle.name == "demo"
