"""Command line interface.

    dbmv snapshot "oracle+oracledb://user:pw@old-host:1521/?service_name=PRD" -o before.json
    dbmv snapshot "oracle+oracledb://user:pw@new-host:1521/?service_name=PRD" -o after.json
    dbmv compare before.json after.json -r report.md

`compare` exits with 1 when there are critical findings, so it can gate a cutover
script or a CI job. Passwords can come from the environment: DBMV_URL is used when
no URL is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .compare import as_dicts, compare, summary
from .report import render
from .snapshot import SnapshotOptions, load, save, take_snapshot


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dbmv", description="Database migration validator")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("snapshot", help="snapshot a database to JSON")
    s.add_argument("url", nargs="?", default=None, help="SQLAlchemy URL (or env DBMV_URL)")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--schema", action="append", help="schema to include (repeatable)")
    s.add_argument("--exclude", action="append", default=[], help="table to skip")
    s.add_argument("--no-hash", action="store_true", help="structure and counts only")
    s.add_argument("--max-rows-for-hash", type=int, default=None)

    c = sub.add_parser("compare", help="compare two snapshots")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("-r", "--report", help="write a Markdown report here")
    c.add_argument("--json", help="write findings as JSON here")
    c.add_argument(
        "--ignore-types",
        action="store_true",
        help="skip column type comparison (cross-engine moves)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "snapshot":
        url = args.url or os.environ.get("DBMV_URL")
        if not url:
            print("error: give a URL or set DBMV_URL", file=sys.stderr)
            return 2
        opts = SnapshotOptions(
            schemas=args.schema or [None],
            exclude_tables=[t.lower() for t in args.exclude],
            hash_content=not args.no_hash,
            max_rows_for_hash=args.max_rows_for_hash,
        )
        snap = take_snapshot(url, opts)
        save(snap, args.out)
        print(f"{len(snap['tables'])} tables, {len(snap['views'])} views -> {args.out}")
        return 0

    before, after = load(args.before), load(args.after)
    findings = compare(before, after, ignore_types=args.ignore_types)
    counts = summary(findings)
    if args.report:
        Path(args.report).write_text(render(before, after, findings), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(as_dicts(findings), indent=2), encoding="utf-8")
    print(" · ".join(f"{k.lower()}: {v}" for k, v in counts.items()))
    return 1 if counts["CRITICAL"] else 0


if __name__ == "__main__":
    sys.exit(main())
