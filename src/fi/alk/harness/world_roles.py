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
# The world's own role, given to the door's processes too: the rows of the world (an actor's record change, an MCP
# tool's statement) are written with it, so a customer trigger fired by them runs with no right on the harness's
# tables. The door's role writes only the harness's tables.
WORLD_RECORDS_DATABASE_URL = "WORLD_RECORDS_DATABASE_URL"
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
    # In a database: a harness table (`_alk_*`) that would run anything but the system's own code when the harness or
    # the door writes it, whoever made it.
    harness_tables: str = ""


# A harness table's rows are written by the superuser and the door: the table may run no code of anyone's. Any
# trigger, rule, row policy, partition or parent, any grant, or anything the table, its defaults, constraints or
# indexes use that the system did not make (a function, type, domain, operator, collation or operator class with an
# oid past the system's own, wherever it lives, `pg_catalog` included) refuses the build.
_HARNESS_FROM = "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
_HARNESS_WHERE = (
    "WHERE c.relname LIKE '\\_alk\\_%' AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg\\_%'"
)
_FIRST_USER_OID = 16384
# A function, operator, type or relation in `pg_catalog` the system did not make and no extension owns: a superuser
# seed can put one there, and a session on the system's own search path then finds it first. Written with exact
# oid and "char" comparisons only, so reading it calls nothing such an object could stand in for; run before
# anything else after the seed.
PG_CATALOG_PLANTS = (
    "SELECT 'function in pg_catalog the system did not make: ' || p.proname::text, p.oid::text FROM pg_proc p "
    f"WHERE p.pronamespace = 11::oid AND p.oid >= {_FIRST_USER_OID}::oid AND NOT EXISTS (SELECT 1 FROM pg_depend d "
    "WHERE d.classid = 1255::oid AND d.objid = p.oid AND d.deptype = 'e'::\"char\") "
    "UNION ALL SELECT 'operator in pg_catalog the system did not make: ' || o.oprname::text, o.oid::text "
    f"FROM pg_operator o WHERE o.oprnamespace = 11::oid AND o.oid >= {_FIRST_USER_OID}::oid AND NOT EXISTS "
    "(SELECT 1 FROM pg_depend d WHERE d.classid = 2617::oid AND d.objid = o.oid AND d.deptype = 'e'::\"char\") "
    "UNION ALL SELECT 'type in pg_catalog the system did not make: ' || t.typname::text, t.oid::text FROM pg_type t "
    f"WHERE t.typnamespace = 11::oid AND t.oid >= {_FIRST_USER_OID}::oid AND NOT EXISTS (SELECT 1 FROM pg_depend d "
    "WHERE d.classid = 1247::oid AND d.objid = t.oid AND d.deptype = 'e'::\"char\") "
    "UNION ALL SELECT 'relation in pg_catalog the system did not make: ' || c.relname::text, c.oid::text "
    f"FROM pg_class c WHERE c.relnamespace = 11::oid AND c.oid >= {_FIRST_USER_OID}::oid AND NOT EXISTS "
    "(SELECT 1 FROM pg_depend d WHERE d.classid = 1259::oid AND d.objid = c.oid AND d.deptype = 'e'::\"char\")"
)
HARNESS_TABLES_WITH_CODE = (
    "SELECT 'harness table ' || c.oid::regclass::text || ' is not a plain table', c.relkind::text "
    f"{_HARNESS_FROM}{_HARNESS_WHERE} AND (c.relkind NOT IN ('r', 'S', 'i') OR c.relispartition "
    "OR EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid OR i.inhparent = c.oid)) "
    "UNION ALL SELECT 'harness table ' || c.oid::regclass::text "
    "|| ' carries a trigger, rule, row policy, grant or extended statistics', "
    "md5(coalesce(c.relacl::text, '') || c.relrowsecurity::text || c.relhasrules::text) "
    f"{_HARNESS_FROM}{_HARNESS_WHERE} AND c.relkind = 'r' AND (c.relacl IS NOT NULL OR c.relrowsecurity OR c.relhasrules "
    "OR EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal) "
    "OR EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) "
    # `ANALYZE` evaluates a statistics object's expressions as whoever runs it.
    "OR EXISTS (SELECT 1 FROM pg_statistic_ext x WHERE x.stxrelid = c.oid)) "
    "UNION ALL SELECT DISTINCT 'harness table ' || c.oid::regclass::text || ' uses code the system did not make: ' "
    "|| pg_describe_object(d.refclassid, d.refobjid, 0), d.refobjid::text "
    f"{_HARNESS_FROM}JOIN pg_depend d ON ("
    "(d.classid = 'pg_class'::regclass AND (d.objid = c.oid "
    "OR d.objid IN (SELECT x.indexrelid FROM pg_index x WHERE x.indrelid = c.oid))) "
    "OR (d.classid = 'pg_attrdef'::regclass AND d.objid IN (SELECT a.oid FROM pg_attrdef a WHERE a.adrelid = c.oid)) "
    "OR (d.classid = 'pg_constraint'::regclass "
    "AND d.objid IN (SELECT k.oid FROM pg_constraint k WHERE k.conrelid = c.oid))) "
    "AND d.refclassid IN ('pg_proc'::regclass, 'pg_type'::regclass, 'pg_operator'::regclass, "
    "'pg_collation'::regclass, 'pg_opclass'::regclass, 'pg_opfamily'::regclass) "
    f"{_HARNESS_WHERE} AND c.relkind = 'r' AND d.refobjid >= {_FIRST_USER_OID}"
)


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
        # A harness table someone else made, or one with a trigger or rule: the harness adopts a table it finds
        # (CREATE TABLE IF NOT EXISTS), and a superuser or the door writing it would run that code.
        " UNION ALL SELECT 'harness table ' || c.oid::regclass::text || ' made by another role or carrying a trigger "
        "or rule', md5(c.relowner::text || coalesce(c.relacl::text, '') || c.relhastriggers::text "
        "|| c.relhasrules::text) "
        "FROM pg_class c JOIN pg_roles o ON o.oid = c.relowner "
        "WHERE c.relname LIKE '\\_alk\\_%' AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND (NOT o.rolsuper OR c.relhastriggers OR c.relhasrules OR EXISTS "
        "(SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal))"
    )
    return SeedEscalationQueries(our_roles, other_roles, code, HARNESS_TABLES_WITH_CODE)


def seed_escalation_reason(process_name: str, found: Iterable[str], made_by: str = "seed") -> str:
    """Why the build is refused, and what to change in the seed or the migrations (``made_by``)."""
    return (
        f"{process_name}: the {made_by} gave rights no world may have: "
        + "; ".join(sorted(set(found)))
        + f". Change the {made_by} so it gives no role SUPERUSER, CREATEROLE, CREATEDB, REPLICATION or BYPASSRLS, puts "
        "no role in another, leaves the harness's roles and _alk_ tables as they are (no trigger, rule or own copy of "
        "one), and defines no event trigger, function in an untrusted language, or SECURITY DEFINER function owned by a "
        "superuser."
    )
