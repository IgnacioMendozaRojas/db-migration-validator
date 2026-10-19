"""Take a JSON snapshot of a database: structure, volumes, content fingerprints, grants.

Content fingerprint
-------------------
Each row is canonicalised and hashed with SHA-256; the table fingerprint is the
*sum* of the first 8 bytes of every row hash, modulo 2^64. Because addition is
commutative the fingerprint does not depend on row order, so no ORDER BY is needed
and engines with different physical ordering still agree. Duplicate rows are
counted, unlike XOR. Per-column fingerprints use the same idea on single values,
which tells you *which* column changed when a table fingerprint differs.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, MetaData, Table, create_engine, func, inspect, select

from .normalize import canonical
from .privileges import collect_privileges

MASK = (1 << 64) - 1


def _h64(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


@dataclass
class SnapshotOptions:
    schemas: list[str | None] = field(default_factory=lambda: [None])
    include_tables: list[str] | None = None
    exclude_tables: list[str] = field(default_factory=list)
    hash_content: bool = True
    max_rows_for_hash: int | None = None  # skip content hashing above this size
    batch_size: int = 10_000


def _type_name(col_type) -> str:
    try:
        return str(col_type).upper()
    except Exception:  # some dialect types cannot compile without a dialect
        return type(col_type).__name__.upper()


def _table_entry(conn, insp, schema, name, opts: SnapshotOptions) -> dict:
    columns = insp.get_columns(name, schema=schema)
    pk = insp.get_pk_constraint(name, schema=schema) or {}
    entry = {
        "columns": {
            c["name"].lower(): {"type": _type_name(c["type"]), "nullable": bool(c["nullable"])}
            for c in columns
        },
        "primary_key": [c.lower() for c in pk.get("constrained_columns") or []],
        "indexes": sorted(
            {
                (i.get("name") or "").lower(): {
                    "columns": [str(c).lower() for c in i.get("column_names") or []],
                    "unique": bool(i.get("unique")),
                }
                for i in insp.get_indexes(name, schema=schema)
            }.items()
        ),
        "foreign_keys": sorted(
            f"{','.join(fk['constrained_columns'])}->{fk['referred_table']}"
            f"({','.join(fk['referred_columns'])})".lower()
            for fk in insp.get_foreign_keys(name, schema=schema)
        ),
    }
    entry["indexes"] = dict(entry["indexes"])

    table = Table(name, MetaData(), schema=schema, autoload_with=conn)
    entry["row_count"] = int(conn.execute(select(func.count()).select_from(table)).scalar_one())

    too_big = opts.max_rows_for_hash is not None and entry["row_count"] > opts.max_rows_for_hash
    if opts.hash_content and not too_big:
        cols = list(table.columns)
        names = [c.name.lower() for c in cols]
        order = sorted(range(len(cols)), key=lambda i: names[i])  # engine-independent order
        table_fp, col_fp = 0, [0] * len(cols)
        nulls = [0] * len(cols)
        result = conn.execution_options(stream_results=True).execute(select(*cols))
        for batch in result.partitions(opts.batch_size):
            for row in batch:
                values = [canonical(row[i]) for i in order]
                table_fp = (table_fp + _h64("\x1f".join(values))) & MASK
                for pos, i in enumerate(order):
                    col_fp[i] = (col_fp[i] + _h64(values[pos])) & MASK
                    if row[i] is None:
                        nulls[i] += 1
        entry["fingerprint"] = f"{table_fp:016x}"
        for i, n in enumerate(names):
            entry["columns"][n]["fingerprint"] = f"{col_fp[i]:016x}"
            entry["columns"][n]["nulls"] = nulls[i]
    else:
        entry["fingerprint"] = None
    return entry


def take_snapshot(engine_or_url: Engine | str, options: SnapshotOptions | None = None) -> dict:
    opts = options or SnapshotOptions()
    engine = create_engine(engine_or_url) if isinstance(engine_or_url, str) else engine_or_url
    insp = inspect(engine)
    snap: dict = {
        "taken_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dialect": engine.dialect.name,
        "server_version": ".".join(map(str, engine.dialect.server_version_info or ())),
        "tables": {},
        "views": {},
        "privileges": None,
    }
    with engine.connect() as conn:
        for schema in opts.schemas:
            prefix = f"{schema.lower()}." if schema else ""
            for name in sorted(insp.get_table_names(schema=schema)):
                if opts.include_tables and name.lower() not in opts.include_tables:
                    continue
                if name.lower() in opts.exclude_tables:
                    continue
                snap["tables"][prefix + name.lower()] = _table_entry(conn, insp, schema, name, opts)
            for view in sorted(insp.get_view_names(schema=schema)):
                definition = insp.get_view_definition(view, schema=schema) or ""
                snap["views"][prefix + view.lower()] = {
                    "definition_hash": hashlib.sha256(
                        " ".join(definition.lower().split()).encode()
                    ).hexdigest()[:16]
                }
        snap["privileges"] = collect_privileges(conn)
    return snap


def save(snapshot: dict, path: Path | str) -> Path:
    path = Path(path)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
