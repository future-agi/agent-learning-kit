"""The digest of a world database's own definitions, kind by kind: what a reset must leave as its snapshot found
it, read on `pg_catalog` alone so each is spelled the same whichever session reads it."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from psycopg import Connection

# Every definition of the world's own, a digest per kind, read on `pg_catalog` alone so each is spelled the same
# whichever session reads it: a reset compares it with its snapshot's before it commits.
_IN_WORLD = "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%%'"
_KINDS: tuple[tuple[str, str], ...] = (
        ("schemas", f"SELECT row_to_json(n)::text FROM pg_namespace n WHERE {_IN_WORLD}"),
        # Every column but the planner's own counts, the vacuum's horizons and relhassubclass, which move on their
        # own (an analyze clears relhassubclass; inheritance itself is read from pg_inherits below).
        ("relations", "SELECT concat_ws('|', n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner), "
         "c.relacl::text, c.relrowsecurity, c.relforcerowsecurity, c.relpersistence, c.reloptions::text, "
         "c.reltype, c.reloftype, c.relam, c.reltablespace, c.relreplident, c.relispartition, "
         "pg_get_expr(c.relpartbound, c.oid), c.relrewrite) "
         f"FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_IN_WORLD}"),
        ("columns", "SELECT concat_ws('|', n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), "
         "a.attnotnull, a.attidentity, a.attgenerated, a.attacl::text, pg_get_expr(d.adbin, d.adrelid), "
         "a.attnum, a.attcollation, a.attstorage, a.attcompression, a.attoptions::text, a.attfdwoptions::text, "
         "a.attislocal, a.attinhcount, a.attstattarget) "
         "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
         "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
         f"WHERE a.attnum > 0 AND NOT a.attisdropped AND {_IN_WORLD}"),
        ("constraints", "SELECT concat_ws('|', n.nspname, k.conname, k.conrelid::oid::text <> '0', "
         "coalesce(c.relname, ''), pg_get_constraintdef(k.oid), k.convalidated, k.contypid, k.conislocal, "
         "k.coninhcount, k.connoinherit, k.condeferrable, k.condeferred) FROM pg_constraint k "
         "JOIN pg_namespace n ON n.oid = k.connamespace LEFT JOIN pg_class c ON c.oid = k.conrelid "
         f"WHERE {_IN_WORLD}"),
        ("indexes", "SELECT concat_ws('|', pg_get_indexdef(i.indexrelid), i.indisclustered, i.indisreplident) "
         "FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
         f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_IN_WORLD}"),
        ("triggers", "SELECT concat_ws('|', n.nspname, c.relname, t.tgname, t.tgenabled, pg_get_triggerdef(t.oid)) "
         "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
         f"WHERE NOT t.tgisinternal AND {_IN_WORLD}"),
        ("rules", "SELECT concat_ws('|', n.nspname, c.relname, r.rulename, r.ev_enabled, pg_get_ruledef(r.oid)) "
         "FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class JOIN pg_namespace n ON n.oid = c.relnamespace "
         f"WHERE {_IN_WORLD}"),
        ("policies", "SELECT concat_ws('|', n.nspname, c.relname, p.polname, p.polcmd, p.polpermissive, "
         "p.polroles::text, pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid)) "
         "FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
         f"WHERE {_IN_WORLD}"),
        # Whole rows from here: a definition is every column of it, not the ones picked to read.
        ("functions", "SELECT row_to_json(p)::text "
         f"FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE {_IN_WORLD}"),
        ("operators", "SELECT row_to_json(o)::text "
         f"FROM pg_operator o JOIN pg_namespace n ON n.oid = o.oprnamespace WHERE {_IN_WORLD}"),
        ("types", "SELECT concat_ws('|', row_to_json(t)::text, (SELECT string_agg(e.enumlabel, ',' "
         "ORDER BY e.enumsortorder) FROM pg_enum e WHERE e.enumtypid = t.oid)) "
         f"FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace WHERE {_IN_WORLD}"),
        ("inheritance", "SELECT concat_ws('|', i.inhrelid::regclass, i.inhparent::regclass, i.inhseqno, "
         "i.inhdetachpending) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
         f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_IN_WORLD}"),
        ("casts", "SELECT row_to_json(c)::text FROM pg_cast c WHERE c.oid >= 16384"),
        ("extensions", "SELECT row_to_json(e)::text FROM pg_extension e"),
        # The database's own settings (ALTER DATABASE ... SET), which every new session of every role takes.
        ("database settings", "SELECT s.setconfig::text FROM pg_db_role_setting s WHERE s.setrole = 0 "
         "AND s.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())"),
        ("database", "SELECT concat_ws('|', pg_get_userbyid(d.datdba), d.datacl::text, d.datallowconn, "
         "d.datconnlimit, d.datistemplate) FROM pg_database d WHERE d.datname = current_database()"),
        # The reset's own role, here and everywhere: the role whose code a reset runs. Another world's roles, and the
        # agent's own role (whose settings each world's reset clears), are no part of this world's reset.
        ("role settings", "SELECT concat_ws('|', s.setdatabase, pg_get_userbyid(s.setrole), s.setconfig::text) "
         "FROM pg_db_role_setting s WHERE s.setrole = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
         "AND s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))"),
        ("event triggers", "SELECT row_to_json(e)::text FROM pg_event_trigger e"),
        # A large object outlives a reset of rows; one made since the snapshot would carry across trials.
        ("large objects", "SELECT concat_ws('|', l.oid, pg_get_userbyid(l.lomowner), l.lomacl::text"
         # Its contents too, where the reset's role may read them: one others may write could carry a note.
         ", CASE WHEN (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) OR l.lomowner = "
         "(SELECT oid FROM pg_roles WHERE rolname = current_user) OR EXISTS (SELECT FROM aclexplode(l.lomacl) a "
         "WHERE a.privilege_type = 'SELECT' AND (a.grantee = 0 OR pg_has_role(current_user, a.grantee, 'USAGE'))) "
         "THEN md5(lo_get(l.oid)) END) FROM pg_largeobject_metadata l"),
        # A sequence's parameters (its step, bounds, cycling), which an owner's ALTER SEQUENCE changes and a reset of
        # its counter does not put back.
        ("sequences", "SELECT row_to_json(s)::text FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid "
         f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_IN_WORLD}"),
        # A comment on any object made after the system's own: a note the world's code could leave for the next trial.
        ("comments", "SELECT concat_ws('|', d.classoid::regclass, d.objoid, d.objsubid, d.description) "
         "FROM pg_description d WHERE d.objoid >= 16384 UNION ALL SELECT concat_ws('|', 'database', d.description) "
         "FROM pg_shdescription d WHERE d.classoid = 'pg_database'::regclass "
         "AND d.objoid = (SELECT oid FROM pg_database WHERE datname = current_database())"),
        # A publication, which code running as a superuser-owned definer could add.
        ("publications", "SELECT concat_ws('|', row_to_json(p)::text, (SELECT string_agg(row_to_json(r)::text, ',' "
         "ORDER BY r.oid) FROM pg_publication_rel r WHERE r.prpubid = p.oid), (SELECT string_agg("
         "row_to_json(s)::text, ',' ORDER BY s.oid) FROM pg_publication_namespace s WHERE s.pnpubid = p.oid)) "
         "FROM pg_publication p"),
        # Default privileges carry a grant to whatever is made next.
        ("default privileges", "SELECT row_to_json(a)::text FROM pg_default_acl a"),
)
# The other kinds of definition a database holds, whole rows of every one made after the system's own (an object id
# of 16384 or more): text search, collations, conversions, extended statistics, operator classes and families,
# languages, transforms, access methods, aggregates and foreign-data definitions.
_KINDS += tuple(
    (kind, f"SELECT row_to_json(x)::text FROM {catalog} x WHERE x.{key}::oid >= 16384")
    for kind, catalog, key in (
        ("text search configurations", "pg_ts_config", "oid"),
        ("text search mappings", "pg_ts_config_map", "mapcfg"),
        ("text search dictionaries", "pg_ts_dict", "oid"),
        ("text search parsers", "pg_ts_parser", "oid"),
        ("text search templates", "pg_ts_template", "oid"),
        ("collations", "pg_collation", "oid"),
        ("conversions", "pg_conversion", "oid"),
        ("extended statistics", "pg_statistic_ext", "oid"),
        ("operator classes", "pg_opclass", "oid"),
        ("operator families", "pg_opfamily", "oid"),
        ("operator family members", "pg_amop", "oid"),
        ("operator family functions", "pg_amproc", "oid"),
        ("languages", "pg_language", "oid"),
        ("transforms", "pg_transform", "oid"),
        ("access methods", "pg_am", "oid"),
        ("aggregates", "pg_aggregate", "aggfnoid"),
        ("foreign data wrappers", "pg_foreign_data_wrapper", "oid"),
        ("foreign servers", "pg_foreign_server", "oid"),
        ("foreign tables", "pg_foreign_table", "ftrelid"),
    )
)
_CATALOG = " UNION ALL ".join(
    f"SELECT %s, md5(coalesce(string_agg(x, E'\\n' ORDER BY x), '')) FROM ({rows}) d(x)" for _kind, rows in _KINDS
)


def catalog_digest(connection: Connection[Any]) -> dict[str, str]:
    """Every definition of the world's own, a digest per kind, spelled on `pg_catalog` alone (call inside a
    transaction: the search path is set for it only)."""
    connection.execute("SET LOCAL search_path = pg_catalog")
    rows = connection.execute(_CATALOG, tuple(kind for kind, _rows in _KINDS)).fetchall()
    return {str(kind): str(digest) for kind, digest in rows}


# State only the harness may read whole, outside the rows a reset puts back: every large object's contents (a world
# role may be let write one it may not read) and every materialized view's contents (code running as the view's owner
# may refresh it). Read on the harness's own connection, after a reset commits as well as at the snapshot.
_LARGE_OBJECTS = (
    "SELECT md5(coalesce(string_agg(concat_ws('|', l.oid, md5(lo_get(l.oid))), E'\\n' ORDER BY l.oid), '')) "
    "FROM pg_largeobject_metadata l"
)
_MATERIALIZED_VIEWS = (
    "SELECT n.nspname, c.relname, c.relispopulated FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    f"WHERE c.relkind = 'm' AND {_IN_WORLD} ORDER BY 1, 2"
)


HARNESS_STATE_KINDS = ("large object contents", "materialized view contents")


def harness_state_digest(connection: Connection[Any]) -> dict[str, str]:
    """The contents of the world's large objects and materialized views, a digest per kind, read as the harness
    (call inside a transaction: the search path is set for it only)."""
    from psycopg import sql

    connection.execute("SET LOCAL search_path = pg_catalog")
    (objects,) = connection.execute(_LARGE_OBJECTS).fetchone() or ("",)
    views = []
    for schema, name, populated in connection.execute(_MATERIALIZED_VIEWS).fetchall():
        contents = "not populated"
        if populated:
            row = connection.execute(
                sql.SQL("SELECT md5(coalesce(string_agg(v::text, E'\\n' ORDER BY v::text), '')) FROM {} v").format(
                    sql.Identifier(str(schema), str(name))
                )
            ).fetchone()
            contents = str(row[0]) if row else ""
        views.append(f"{schema}.{name}|{contents}")
    contents = str(objects), hashlib.md5("\n".join(views).encode(), usedforsecurity=False).hexdigest()
    return dict(zip(HARNESS_STATE_KINDS, contents, strict=True))
