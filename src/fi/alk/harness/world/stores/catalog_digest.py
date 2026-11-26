"""The digest of a world database's own definitions, kind by kind: what a reset must leave as its snapshot found
it, read on `pg_catalog` alone so each is spelled the same whichever session reads it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from psycopg import Connection

# Every definition of the world's own, a digest per kind, read on `pg_catalog` alone so each is spelled the same
# whichever session reads it: a reset compares it with its snapshot's before it commits.
_IN_WORLD = "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%%'"
_KINDS: tuple[tuple[str, str], ...] = (
        ("schemas", f"SELECT concat_ws('|', n.nspname, pg_get_userbyid(n.nspowner), n.nspacl::text) "
         f"FROM pg_namespace n WHERE {_IN_WORLD}"),
        ("relations", "SELECT concat_ws('|', n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner), "
         "c.relacl::text, c.relrowsecurity, c.relforcerowsecurity, c.relpersistence, c.reloptions::text) "
         f"FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_IN_WORLD}"),
        ("columns", "SELECT concat_ws('|', n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), "
         "a.attnotnull, a.attidentity, a.attgenerated, a.attacl::text, pg_get_expr(d.adbin, d.adrelid)) "
         "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
         "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
         f"WHERE a.attnum > 0 AND NOT a.attisdropped AND {_IN_WORLD}"),
        ("constraints", "SELECT concat_ws('|', n.nspname, k.conname, k.conrelid::oid::text <> '0', "
         "coalesce(c.relname, ''), pg_get_constraintdef(k.oid), k.convalidated) FROM pg_constraint k "
         "JOIN pg_namespace n ON n.oid = k.connamespace LEFT JOIN pg_class c ON c.oid = k.conrelid "
         f"WHERE {_IN_WORLD}"),
        ("indexes", "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
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
        ("functions", "SELECT concat_ws('|', n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), "
         "pg_get_function_result(p.oid), pg_get_userbyid(p.proowner), p.prosecdef, p.proacl::text, "
         "p.proconfig::text, p.provolatile, p.proleakproof, p.prolang, p.prosrc, p.probin) "
         f"FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE {_IN_WORLD}"),
        ("operators", "SELECT concat_ws('|', n.nspname, o.oprname, format_type(o.oprleft, NULL), "
         "format_type(o.oprright, NULL), o.oprcode::oid, pg_get_userbyid(o.oprowner)) "
         f"FROM pg_operator o JOIN pg_namespace n ON n.oid = o.oprnamespace WHERE {_IN_WORLD}"),
        ("types", "SELECT concat_ws('|', n.nspname, t.typname, t.typtype, pg_get_userbyid(t.typowner), "
         "t.typacl::text, (SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) FROM pg_enum e "
         f"WHERE e.enumtypid = t.oid)) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace WHERE {_IN_WORLD}"),
        ("casts", "SELECT concat_ws('|', c.castsource, c.casttarget, c.castfunc, c.castcontext) FROM pg_cast c "
         "WHERE c.oid >= 16384"),
        ("extensions", "SELECT concat_ws('|', e.extname, e.extversion, e.extnamespace) FROM pg_extension e"),
        # The reset's own role, here and everywhere: the role whose code a reset runs. Another world's roles, and the
        # agent's own role (whose settings each world's reset clears), are no part of this world's reset.
        ("role settings", "SELECT concat_ws('|', s.setdatabase, pg_get_userbyid(s.setrole), s.setconfig::text) "
         "FROM pg_db_role_setting s WHERE s.setrole = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
         "AND s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))"),
        ("event triggers", "SELECT concat_ws('|', e.evtname, e.evtevent, e.evtenabled, e.evtfoid) "
         "FROM pg_event_trigger e"),
        # A large object outlives a reset of rows; one made since the snapshot would carry across trials.
        ("large objects", "SELECT concat_ws('|', l.oid, pg_get_userbyid(l.lomowner), l.lomacl::text"
         # Its contents too, where the reset's role may read them: one others may write could carry a note.
         ", CASE WHEN (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) OR l.lomowner = "
         "(SELECT oid FROM pg_roles WHERE rolname = current_user) OR EXISTS (SELECT FROM aclexplode(l.lomacl) a "
         "WHERE a.privilege_type = 'SELECT' AND (a.grantee = 0 OR pg_has_role(current_user, a.grantee, 'USAGE'))) "
         "THEN md5(lo_get(l.oid)) END) FROM pg_largeobject_metadata l"),
        # Default privileges carry a grant to whatever is made next.
        ("default privileges", "SELECT concat_ws('|', pg_get_userbyid(a.defaclrole), a.defaclnamespace, "
         "a.defaclobjtype, a.defaclacl::text) FROM pg_default_acl a"),
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
