"""The roles a world's database gives the world's door and each world, and what the seed may not leave behind.

Process isolation gives each world's processes a login role of their own; the door, which keeps the call log grading
reads, gets one more. The runtime makes and grants them (`process_runtime`); this module names them, says what the door
may do on the harness's tables, and checks a seed for a way around them."""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

from .bundle_v2 import EnvironmentBundleV2, ProcessUser

# The world's door (`svc-door`) reaches the database through a role of its own, in this group: a member of the world
# group, so it changes the world's rows as the agent's tools do, and the only one with rights on the harness's tables.
DOOR_ROLE_GROUP = "alk_door"
# Each world's own login role, and its door's, named by the world's index after these.
WORLD_ROLE_PREFIX = "alk_w"
DOOR_ROLE_PREFIX = "alk_d"
# The door's address, rendered only into the door's own processes.
WORLD_DOOR_DATABASE_URL = "WORLD_DOOR_DATABASE_URL"
# The harness's tables in a world database, and what the door may do on each. The agent's own tools have no right on
# any of them: they hold the call log grading reads, who may do what, and this trial's faults.
DOOR_TABLE_RIGHTS = {
    "_alk_tool_trace": "SELECT, INSERT",
    "_alk_action_keys": "SELECT, INSERT, UPDATE, DELETE",
    "_alk_actors": "SELECT",
    # A worker stop locks this trial's row, which takes UPDATE.
    "_alk_trial_faults": "SELECT, UPDATE",
    # A stop or a run replaces a row an earlier trial left.
    "_alk_worker_stops": "SELECT, INSERT, UPDATE",
    "_alk_worker_runs": "SELECT, INSERT, UPDATE",
}
# The harness's own marker table, which no world role may touch.
HARNESS_ONLY_TABLES = ("harness_seed_sentinel",)


def world_role_name(world_index: int) -> str:
    """World N's own login role."""
    return f"{WORLD_ROLE_PREFIX}{world_index}"


def door_role_name(world_index: int) -> str:
    """World N's door's own login role."""
    return f"{DOOR_ROLE_PREFIX}{world_index}"


def has_world_door(manifest: EnvironmentBundleV2) -> bool:
    """Whether this bundle runs its world's door as `svc-door`; a bundle sealed before it keeps one role per world."""
    return any(process.user is ProcessUser.SVC_DOOR for process in manifest.processes)


class SeedEscalationQueries(NamedTuple):
    """The read-only queries that find a way to more rights than a world's role has. Each row is (what was found, its
    fingerprint): the fingerprint changes when the object does, so a seed that changes an object the source's own
    migrations made is caught as well as one that adds one."""

    # Our roles, made after the seed: anything one of them already has is planted, whoever planted it.
    our_roles: str
    # Any other role that is a superuser or can make roles, cluster-wide.
    other_roles: str
    # In a database: code that runs as its owner whoever calls it, or on any DDL.
    code: str


def seed_escalation_queries(world_group: str) -> SeedEscalationQueries:
    """The queries for a world group ``world_group``, the group every world's role is a member of."""
    our_role = (
        f"(r.rolname IN ('{world_group}', '{DOOR_ROLE_GROUP}') "
        f"OR r.rolname ~ '^({WORLD_ROLE_PREFIX}|{DOOR_ROLE_PREFIX})[0-9]+$')"
    )
    our_roles = (
        "SELECT 'role ' || r.rolname || ' has an attribute the harness never gives it', '' "
        f"FROM pg_roles r WHERE {our_role} "
        "AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls) "
        "UNION ALL SELECT 'role ' || r.rolname || ' is a member of ' || g.rolname, '' "
        "FROM pg_auth_members a JOIN pg_roles r ON r.oid = a.member JOIN pg_roles g ON g.oid = a.roleid "
        f"WHERE {our_role} AND NOT ("
        f"(g.rolname = '{world_group}' AND (r.rolname ~ '^{WORLD_ROLE_PREFIX}[0-9]+$' OR r.rolname = '{DOOR_ROLE_GROUP}')) "
        f"OR (g.rolname = '{DOOR_ROLE_GROUP}' AND r.rolname ~ '^{DOOR_ROLE_PREFIX}[0-9]+$'))"
    )
    other_roles = (
        "SELECT 'role ' || rolname || ' is a superuser or can create roles', "
        "rolsuper::text || rolcreaterole::text FROM pg_roles "
        "WHERE (rolsuper OR rolcreaterole) AND rolname <> current_user"
    )
    # A SECURITY DEFINER function runs as its owner: it is a way to more rights only when that owner is a superuser or
    # holds a right on a harness table. One the migrations' login owns (no superuser, no harness right) runs as that.
    owner_reaches = (
        "(o.rolsuper OR EXISTS (SELECT 1 FROM pg_class c WHERE c.relkind IN ('r', 'p', 'v', 'm') "
        "AND c.relname LIKE '\\_alk\\_%' AND has_table_privilege(o.oid, c.oid, 'SELECT, INSERT, UPDATE, DELETE')))"
    )
    code = (
        "SELECT 'event trigger ' || evtname, "
        "md5(evtevent || evtfoid::text || evtenabled::text || evtowner::text || coalesce(evttags::text, '')) "
        "FROM pg_event_trigger "
        "UNION ALL SELECT CASE WHEN p.prosecdef THEN 'security definer function ' "
        "ELSE 'function in untrusted language ' || l.lanname || ' ' END || p.oid::regprocedure::text, "
        "md5(p.prosrc || coalesce(p.probin, '') || p.proowner::text || coalesce(p.proconfig::text, '') "
        "|| coalesce(p.proacl::text, '')) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang "
        "JOIN pg_roles o ON o.oid = p.proowner "
        "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
        f"AND ((p.prosecdef AND {owner_reaches}) OR (NOT l.lanpltrusted AND l.lanname NOT IN ('internal', 'c')))"
    )
    return SeedEscalationQueries(our_roles, other_roles, code)


def seed_escalation_reason(process_name: str, found: Iterable[str]) -> str:
    """Why the build is refused, and what to change in the seed."""
    return (
        f"{process_name}: the seed gave rights no world may have: "
        + "; ".join(sorted(set(found)))
        + ". Change the seed so it gives no role SUPERUSER, CREATEROLE, CREATEDB, REPLICATION or BYPASSRLS, puts "
        "no role in another, leaves the harness's roles as they are, and defines no event trigger, function in an "
        "untrusted language, or SECURITY DEFINER function owned by a superuser."
    )
