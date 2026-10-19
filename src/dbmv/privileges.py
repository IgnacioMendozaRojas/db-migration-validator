"""Object privileges (who can do what on which table), per dialect.

Each collector returns rows of (grantee, schema, object, privilege, grantable).
Lost grants are the classic silent failure of a migration: the data arrives intact
but the reporting user or the batch account can no longer read it.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

QUERIES = {
    "postgresql": """
        SELECT grantee, table_schema, table_name, privilege_type, is_grantable
        FROM information_schema.table_privileges
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    """,
    "mssql": """
        SELECT pr.name, s.name, o.name, p.permission_name,
               CASE WHEN p.state = 'W' THEN 'YES' ELSE 'NO' END
        FROM sys.database_permissions p
        JOIN sys.objects o ON p.major_id = o.object_id
        JOIN sys.schemas s ON o.schema_id = s.schema_id
        JOIN sys.database_principals pr ON p.grantee_principal_id = pr.principal_id
        WHERE p.class = 1 AND p.state IN ('G', 'W')
    """,
    "oracle": """
        SELECT grantee, owner, table_name, privilege, grantable
        FROM all_tab_privs
        WHERE owner NOT IN ('SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'ORDSYS')
    """,
    "mysql": """
        SELECT grantee, table_schema, table_name, privilege_type, is_grantable
        FROM information_schema.table_privileges
    """,
}


def collect_privileges(conn: Connection) -> list[dict] | None:
    """Return grants, or None when the engine has no grant system (e.g. SQLite)."""
    query = QUERIES.get(conn.dialect.name)
    if query is None:
        return None
    rows = conn.execute(text(query)).fetchall()
    return sorted(
        (
            {
                "grantee": str(g),
                "schema": str(s),
                "object": str(o),
                "privilege": str(p).upper(),
                "grantable": str(gr).upper() in ("YES", "TRUE", "1"),
            }
            for g, s, o, p, gr in rows
        ),
        key=lambda r: (r["schema"], r["object"], r["grantee"], r["privilege"]),
    )
