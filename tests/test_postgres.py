"""Integration tests against a real PostgreSQL server.

Run with DBMV_PG_URL pointing at a server where the user can create databases and
roles, e.g. postgresql+psycopg://postgres@localhost:5432/postgres. Skipped otherwise.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from test_validator import make_db

from dbmv import Severity, compare, take_snapshot

PG_URL = os.environ.get("DBMV_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="set DBMV_PG_URL to run")

DDL = [
    """CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL,
        segment CHAR(10), credit_limit NUMERIC(12,2), opened DATE)""",
    """CREATE TABLE accounts (id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        balance NUMERIC(14,2), updated_at TIMESTAMP)""",
    "CREATE INDEX ix_accounts_customer ON accounts(customer_id)",
    """CREATE VIEW v_balances AS SELECT customer_id, SUM(balance) AS total
        FROM accounts GROUP BY customer_id""",
]


@pytest.fixture
def pg_database():
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    created = []

    def make(load_from_sqlite: str, grants=("reporting", "batch")):
        name = f"dbmv_{uuid.uuid4().hex[:8]}"
        with admin.connect() as c:
            c.execute(text(f"CREATE DATABASE {name}"))
            for role in ("reporting", "batch"):
                c.execute(
                    text(
                        f"DO $$ BEGIN CREATE ROLE {role}; "
                        f"EXCEPTION WHEN duplicate_object THEN NULL; END $$"
                    )
                )
        created.append(name)
        eng = create_engine(PG_URL.rsplit("/", 1)[0] + f"/{name}")
        src = create_engine(f"sqlite:///{load_from_sqlite}")
        with eng.begin() as c, src.connect() as s:
            for ddl in DDL:
                c.execute(text(ddl))
            for table in ("customers", "accounts"):
                rows = [dict(r._mapping) for r in s.execute(text(f"SELECT * FROM {table}"))]
                cols = ", ".join(rows[0])
                vals = ", ".join(f":{k}" for k in rows[0])
                c.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({vals})"), rows)
            for role in grants:
                c.execute(text(f"GRANT SELECT ON accounts, customers TO {role}"))
        return eng

    yield make
    with admin.connect() as c:
        for name in created:
            c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))


def test_cross_engine_copy_matches(tmp_path, pg_database):
    sqlite_path = tmp_path / "legacy.db"
    legacy = take_snapshot(make_db(sqlite_path))
    target = take_snapshot(pg_database(str(sqlite_path)))
    critical = [
        f for f in compare(legacy, target, ignore_types=True) if f.severity == Severity.CRITICAL
    ]
    assert critical == [], "same data must fingerprint identically on SQLite and PostgreSQL"


def test_detects_lost_grant_and_changed_value(tmp_path, pg_database):
    sqlite_path = tmp_path / "legacy.db"
    make_db(sqlite_path)
    before = take_snapshot(pg_database(str(sqlite_path)))
    new = pg_database(str(sqlite_path), grants=("reporting",))
    with new.begin() as c:
        c.execute(text("UPDATE accounts SET balance = 0 WHERE id = 7"))
    after = take_snapshot(new)

    found = {(f.severity, f.category) for f in compare(before, after)}
    assert (Severity.CRITICAL, "privilege") in found
    assert (Severity.CRITICAL, "content") in found
    lost = [f for f in compare(before, after) if f.category == "privilege"]
    assert {f.detail for f in lost} == {"SELECT for batch lost"}
