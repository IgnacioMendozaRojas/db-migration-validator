"""Simulate a migration with the usual silent failures and produce the report.

    DBMV_PG_URL=postgresql+psycopg://postgres@localhost:5432/postgres python examples/demo.py

Source and target are two PostgreSQL databases. The "migrated" target has:
  - 3 accounts that did not make it (a batch that failed mid-load)
  - balances rounded to whole units by a type change NUMERIC(14,2) -> NUMERIC(14,0)
  - the index on accounts(customer_id) not recreated
  - SELECT for the nightly batch role not re-granted
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_validator import make_db  # noqa: E402

from dbmv import compare, render, take_snapshot  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs"


def copy_to_pg(admin_url: str, sqlite_path: Path, broken: bool) -> tuple[str, object]:
    name = f"demo_{'target' if broken else 'source'}_{uuid.uuid4().hex[:6]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
        for role in ("reporting", "nightly_batch"):
            c.execute(
                text(
                    f"DO $$ BEGIN CREATE ROLE {role}; "
                    "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
                )
            )
    eng = create_engine(admin_url.rsplit("/", 1)[0] + f"/{name}")
    src = create_engine(f"sqlite:///{sqlite_path}")
    balance_type = "NUMERIC(14,0)" if broken else "NUMERIC(14,2)"
    with eng.begin() as c, src.connect() as s:
        c.execute(
            text("""CREATE TABLE customers (id INTEGER PRIMARY KEY,
            name VARCHAR(80) NOT NULL, segment CHAR(10), credit_limit NUMERIC(12,2),
            opened DATE)""")
        )
        c.execute(
            text(f"""CREATE TABLE accounts (id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            balance {balance_type}, updated_at TIMESTAMP)""")
        )
        if not broken:
            c.execute(text("CREATE INDEX ix_accounts_customer ON accounts(customer_id)"))
        c.execute(
            text("""CREATE VIEW v_balances AS SELECT customer_id,
            SUM(balance) AS total FROM accounts GROUP BY customer_id""")
        )
        for table in ("customers", "accounts"):
            rows = [dict(r._mapping) for r in s.execute(text(f"SELECT * FROM {table}"))]
            if broken and table == "accounts":
                rows = [r for r in rows if r["id"] not in (311, 312, 313)]
            cols = ", ".join(rows[0])
            vals = ", ".join(f":{k}" for k in rows[0])
            c.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({vals})"), rows)
        roles = ("reporting",) if broken else ("reporting", "nightly_batch")
        for role in roles:
            c.execute(text(f"GRANT SELECT ON accounts, customers, v_balances TO {role}"))
    return name, eng


def main() -> None:
    url = os.environ.get("DBMV_PG_URL")
    if not url:
        sys.exit("set DBMV_PG_URL to a PostgreSQL server")
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy.db"
        make_db(legacy)
        names = []
        try:
            name_s, source = copy_to_pg(url, legacy, broken=False)
            name_t, target = copy_to_pg(url, legacy, broken=True)
            names = [name_s, name_t]
            before, after = take_snapshot(source), take_snapshot(target)
            source.dispose()
            target.dispose()
        finally:
            admin = create_engine(url, isolation_level="AUTOCOMMIT")
            with admin.connect() as c:
                for n in names:
                    c.execute(text(f"DROP DATABASE IF EXISTS {n} WITH (FORCE)"))
    for snap in (before, after):
        snap["taken_at"] = (
            "2026-03-14T02:00:00+00:00" if snap is before else "2026-03-14T06:30:00+00:00"
        )
    report = render(before, after, compare(before, after))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "sample_report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
