"""Compare a 'before' and an 'after' snapshot and classify every difference.

Severity
--------
CRITICAL  data or access is lost or altered: missing table, row-count or content
          mismatch, missing column, lost grant
WARNING   structure drifted but data may be intact: type or nullability change,
          missing index or foreign key, changed view definition
INFO      additions on the target (new tables, columns, grants) and type renames
          that are known to be equivalent across engines
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

# Type names that differ by engine but hold the same data. Compared after mapping.
EQUIVALENT_TYPES = {
    "VARCHAR2": "VARCHAR",
    "NVARCHAR2": "NVARCHAR",
    "NVARCHAR": "VARCHAR",
    "CHARACTER VARYING": "VARCHAR",
    "NUMBER": "NUMERIC",
    "DECIMAL": "NUMERIC",
    "INT": "INTEGER",
    "DOUBLE PRECISION": "FLOAT",
    "BINARY_DOUBLE": "FLOAT",
    "DATETIME": "TIMESTAMP",
    "DATETIME2": "TIMESTAMP",
    "CLOB": "TEXT",
}


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class Finding:
    severity: Severity
    category: str
    object: str
    detail: str


def _base_type(t: str) -> str:
    base = t.split("(")[0].strip().upper()
    return EQUIVALENT_TYPES.get(base, base)


def _type_params(t: str) -> tuple[int, ...]:
    """Length / precision / scale, e.g. NUMERIC(14, 2) -> (14, 2)."""
    if "(" not in t:
        return ()
    inner = t.split("(", 1)[1].split(")", 1)[0]
    return tuple(int(p) for p in inner.replace(" ", "").split(",") if p.isdigit())


def _type_change_severity(old: str, new: str) -> Severity:
    if _base_type(old) != _base_type(new):
        return Severity.WARNING
    po, pn = _type_params(old), _type_params(new)
    if po and pn and any(n < o for o, n in zip(po, pn, strict=False)):
        return Severity.WARNING  # narrower length, precision or scale: values may be cut
    return Severity.INFO


def compare(before: dict, after: dict, ignore_types: bool = False) -> list[Finding]:
    out: list[Finding] = []
    add = lambda *a: out.append(Finding(*a))  # noqa: E731
    bt, at = before["tables"], after["tables"]

    for name in sorted(set(bt) - set(at)):
        add(Severity.CRITICAL, "table", name, "missing on target")
    for name in sorted(set(at) - set(bt)):
        add(Severity.INFO, "table", name, "new on target")

    for name in sorted(set(bt) & set(at)):
        b, a = bt[name], at[name]
        if b["row_count"] != a["row_count"]:
            diff = a["row_count"] - b["row_count"]
            add(
                Severity.CRITICAL,
                "rows",
                name,
                f"row count {b['row_count']:,} -> {a['row_count']:,} ({diff:+,})",
            )

        bc, ac = b["columns"], a["columns"]
        for col in sorted(set(bc) - set(ac)):
            add(Severity.CRITICAL, "column", f"{name}.{col}", "missing on target")
        for col in sorted(set(ac) - set(bc)):
            add(Severity.INFO, "column", f"{name}.{col}", "new on target")
        for col in sorted(set(bc) & set(ac)):
            x, y = bc[col], ac[col]
            if not ignore_types and x["type"] != y["type"]:
                sev = _type_change_severity(x["type"], y["type"])
                add(sev, "type", f"{name}.{col}", f"{x['type']} -> {y['type']}")
            if x["nullable"] != y["nullable"]:
                add(
                    Severity.WARNING,
                    "nullability",
                    f"{name}.{col}",
                    f"nullable {x['nullable']} -> {y['nullable']}",
                )

        if b["primary_key"] != a["primary_key"]:
            add(
                Severity.WARNING,
                "primary_key",
                name,
                f"{b['primary_key'] or 'none'} -> {a['primary_key'] or 'none'}",
            )
        b_idx = {tuple(v["columns"]): v["unique"] for v in b["indexes"].values()}
        a_idx = {tuple(v["columns"]): v["unique"] for v in a["indexes"].values()}
        for cols in sorted(set(b_idx) - set(a_idx)):
            kind = "unique index" if b_idx[cols] else "index"
            add(Severity.WARNING, "index", name, f"{kind} on ({', '.join(cols)}) missing")
        for fk in sorted(set(b["foreign_keys"]) - set(a["foreign_keys"])):
            add(Severity.WARNING, "foreign_key", name, f"{fk} missing")

        if b.get("fingerprint") and a.get("fingerprint") and b["fingerprint"] != a["fingerprint"]:
            changed = [
                c
                for c in sorted(set(bc) & set(ac))
                if bc[c].get("fingerprint") != ac[c].get("fingerprint")
            ]
            if b["row_count"] != a["row_count"]:
                where = " (row counts differ, so every column is affected)"
            elif changed:
                where = f"; columns differing: {', '.join(changed)}"
            else:
                where = ""
            add(Severity.CRITICAL, "content", name, f"content fingerprint differs{where}")
            for c in changed:
                if bc[c].get("nulls") != ac[c].get("nulls"):
                    add(
                        Severity.CRITICAL,
                        "nulls",
                        f"{name}.{c}",
                        f"null count {bc[c]['nulls']:,} -> {ac[c]['nulls']:,}",
                    )

    for name in sorted(set(before["views"]) - set(after["views"])):
        add(Severity.CRITICAL, "view", name, "missing on target")
    for name in sorted(set(before["views"]) & set(after["views"])):
        if before["views"][name] != after["views"][name]:
            add(Severity.WARNING, "view", name, "definition changed")

    bp, ap = before.get("privileges"), after.get("privileges")
    if bp is not None and ap is not None:

        def key(g: dict) -> tuple[str, str, str, str]:
            return (g["grantee"].lower(), g["schema"].lower(), g["object"].lower(), g["privilege"])

        bmap, amap = {key(g): g for g in bp}, {key(g): g for g in ap}
        for k in sorted(set(bmap) - set(amap)):
            g = bmap[k]
            add(
                Severity.CRITICAL,
                "privilege",
                f"{g['schema']}.{g['object']}",
                f"{g['privilege']} for {g['grantee']} lost",
            )
        for k in sorted(set(amap) - set(bmap)):
            g = amap[k]
            add(
                Severity.INFO,
                "privilege",
                f"{g['schema']}.{g['object']}",
                f"{g['privilege']} for {g['grantee']} new on target",
            )
    elif bp is not None and ap is None:
        add(Severity.WARNING, "privilege", "*", "target engine does not expose grants")

    order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
    return sorted(out, key=lambda f: (order[f.severity], f.category, f.object))


def summary(findings: list[Finding]) -> dict[str, int]:
    return {s.value: sum(f.severity == s for f in findings) for s in Severity}


def as_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) | {"severity": f.severity.value} for f in findings]
