"""Validate a database migration: structure, row counts, content and grants."""

from .compare import Finding, Severity, compare, summary
from .report import render
from .snapshot import SnapshotOptions, load, save, take_snapshot

__version__ = "0.1.0"

__all__ = [
    "Finding",
    "Severity",
    "SnapshotOptions",
    "compare",
    "load",
    "render",
    "save",
    "summary",
    "take_snapshot",
]
