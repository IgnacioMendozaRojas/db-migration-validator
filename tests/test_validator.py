import copy
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from dbmv import Severity, compare, render, take_snapshot
from dbmv.cli import main
from dbmv.normalize import canonical

SCHEMA = [
    """CREATE TABLE customers (
        id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL, segment CHAR(10),
        credit_limit NUMERIC(12,2), opened DATE)""",
    """CREATE TABLE accounts (
        id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
        balance NUMERIC(14,2), updated_at TIMESTAMP)""",
    "CREATE INDEX ix_accounts_customer ON accounts(customer_id)",
    "CREATE VIEW v_balances AS SELECT customer_id, SUM(balance) AS total FROM accounts GROUP BY customer_id",
]
CUSTOMERS = [
    (i, f"Customer {i}", "RETAIL" if i % 3 else "SME", 1000.5 * i, "2024-01-15")
    for i in range(1, 201)
]
ACCOUNTS = [(i, (i % 200) + 1, 25.75 * i, "2025-06-30 10:15:00") for i in range(1, 501)]


def make_db(path, reverse=False, mutate=None):
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as c:
        for ddl in SCHEMA:
            c.execute(text(ddl))
        rows_c = CUSTOMERS[::-1] if reverse else CUSTOMERS
        rows_a = ACCOUNTS[::-1] if reverse else ACCOUNTS
        c.execute(
            text("INSERT INTO customers VALUES (:a,:b,:c,:d,:e)"),
            [dict(zip("abcde", r, strict=True)) for r in rows_c],
        )
        c.execute(
            text("INSERT INTO accounts VALUES (:a,:b,:c,:d)"),
            [dict(zip("abcd", r, strict=True)) for r in rows_a],
        )
        if mutate:
            mutate(c)
    return eng


@pytest.fixture
def source(tmp_path):
    return take_snapshot(make_db(tmp_path / "src.db"))


def kinds(findings, severity):
    return {f.category for f in findings if f.severity == severity}


def test_identical_copy_passes(tmp_path, source):
    target = take_snapshot(make_db(tmp_path / "dst.db"))
    assert compare(source, target) == []
    assert "**PASS**" in render(source, target, [])


def test_fingerprint_ignores_row_order(tmp_path, source):
    target = take_snapshot(make_db(tmp_path / "dst.db", reverse=True))
    assert (
        target["tables"]["accounts"]["fingerprint"] == source["tables"]["accounts"]["fingerprint"]
    )
    assert compare(source, target) == []


def test_lost_rows_are_critical(tmp_path, source):
    target = take_snapshot(
        make_db(
            tmp_path / "dst.db",
            mutate=lambda c: c.execute(text("DELETE FROM accounts WHERE id > 495")),
        )
    )
    f = compare(source, target)
    assert "rows" in kinds(f, Severity.CRITICAL)
    assert any("500 -> 495 (-5)" in x.detail for x in f)


def test_silent_value_change_is_located(tmp_path, source):
    # Same row count, one balance altered: only a content check catches this
    target = take_snapshot(
        make_db(
            tmp_path / "dst.db",
            mutate=lambda c: c.execute(
                text("UPDATE accounts SET balance = balance + 0.01 WHERE id = 42")
            ),
        )
    )
    f = compare(source, target)
    content = [x for x in f if x.category == "content"]
    assert content and "balance" in content[0].detail and "updated_at" not in content[0].detail
    assert "rows" not in kinds(f, Severity.CRITICAL)


def test_nulls_introduced_are_reported(tmp_path, source):
    target = take_snapshot(
        make_db(
            tmp_path / "dst.db",
            mutate=lambda c: c.execute(text("UPDATE customers SET segment = NULL WHERE id <= 3")),
        )
    )
    f = compare(source, target)
    assert any(x.category == "nulls" and x.object == "customers.segment" for x in f)


def test_missing_objects(tmp_path, source):
    def drop(c):
        c.execute(text("DROP VIEW v_balances"))
        c.execute(text("DROP INDEX ix_accounts_customer"))

    target = take_snapshot(make_db(tmp_path / "dst.db", mutate=drop))
    f = compare(source, target)
    assert "view" in kinds(f, Severity.CRITICAL)
    assert "index" in kinds(f, Severity.WARNING)


def test_type_changes_and_equivalences(source):
    target = copy.deepcopy(source)
    target["tables"]["customers"]["columns"]["name"]["type"] = "VARCHAR2(80)"
    target["tables"]["customers"]["columns"]["opened"]["type"] = "VARCHAR(10)"
    f = {x.object: x.severity for x in compare(source, target) if x.category == "type"}
    assert f["customers.name"] == Severity.INFO  # VARCHAR -> VARCHAR2 is equivalent
    assert f["customers.opened"] == Severity.WARNING  # DATE -> VARCHAR is not
    assert compare(source, target, ignore_types=True) == []


def test_narrower_type_is_a_warning(source):
    target = copy.deepcopy(source)
    target["tables"]["accounts"]["columns"]["balance"]["type"] = "NUMERIC(14, 0)"
    target["tables"]["customers"]["columns"]["name"]["type"] = "VARCHAR(120)"
    f = {x.object: x.severity for x in compare(source, target) if x.category == "type"}
    assert f["accounts.balance"] == Severity.WARNING  # scale 2 -> 0 drops decimals
    assert f["customers.name"] == Severity.INFO  # widening is safe


def test_lost_grants_are_critical(source):
    grant = {
        "grantee": "REPORTING",
        "schema": "risk",
        "object": "accounts",
        "privilege": "SELECT",
        "grantable": False,
    }
    before = source | {"privileges": [grant]}
    after = source | {"privileges": []}
    f = compare(before, after)
    assert [(x.severity, x.category) for x in f] == [(Severity.CRITICAL, "privilege")]
    assert "SELECT for REPORTING lost" in f[0].detail


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (Decimal("1"), Decimal("1.00")),
        (Decimal("12.50"), Decimal("12.5")),
        (dt.datetime(2024, 1, 15), dt.date(2024, 1, 15)),  # Oracle DATE vs ANSI DATE
        ("RETAIL    ", "RETAIL"),  # CHAR padding
        (3.0, 3),
        (0.1 + 0.2, 0.3),
    ],
)
def test_canonical_values_are_engine_neutral(a, b):
    assert canonical(a) == canonical(b)


def test_cli_end_to_end(tmp_path):
    make_db(tmp_path / "a.db")
    make_db(
        tmp_path / "b.db", mutate=lambda c: c.execute(text("DELETE FROM customers WHERE id = 7"))
    )
    for name in ("a", "b"):
        assert (
            main(
                [
                    "snapshot",
                    f"sqlite:///{tmp_path / name}.db",
                    "-o",
                    str(tmp_path / f"{name}.json"),
                ]
            )
            == 0
        )
    code = main(
        [
            "compare",
            str(tmp_path / "a.json"),
            str(tmp_path / "b.json"),
            "-r",
            str(tmp_path / "r.md"),
            "--json",
            str(tmp_path / "f.json"),
        ]
    )
    assert code == 1
    assert "**FAIL**" in (tmp_path / "r.md").read_text()
    assert main(["compare", str(tmp_path / "a.json"), str(tmp_path / "a.json")]) == 0
