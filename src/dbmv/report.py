"""Markdown report: verdict first, then evidence grouped by severity."""

from __future__ import annotations

from .compare import Finding, Severity, summary

ICON = {Severity.CRITICAL: "✗", Severity.WARNING: "⚠", Severity.INFO: "ℹ"}


def render(before: dict, after: dict, findings: list[Finding]) -> str:
    s = summary(findings)
    verdict = (
        "**FAIL**: data or access differs between source and target."
        if s["CRITICAL"]
        else "**PASS with warnings**: data and access match; review the structural drift below."
        if s["WARNING"]
        else "**PASS**: source and target match."
    )
    bt, at = before["tables"], after["tables"]
    rows_b = sum(t["row_count"] for t in bt.values())
    rows_a = sum(t["row_count"] for t in at.values())
    hashed = sum(1 for t in bt.values() if t.get("fingerprint"))
    lines = [
        "# Migration validation report",
        "",
        f"{verdict}",
        "",
        "| | Source | Target |",
        "|---|---|---|",
        f"| Engine | {before['dialect']} {before.get('server_version', '')} | "
        f"{after['dialect']} {after.get('server_version', '')} |",
        f"| Snapshot taken | {before['taken_at']} | {after['taken_at']} |",
        f"| Tables | {len(bt)} | {len(at)} |",
        f"| Views | {len(before['views'])} | {len(after['views'])} |",
        f"| Rows | {rows_b:,} | {rows_a:,} |",
        f"| Grants | {len(before['privileges'] or [])} | {len(after['privileges'] or [])} |",
        "",
        f"Content fingerprints compared for {hashed} of {len(bt)} source tables.",
        "",
        f"**{s['CRITICAL']} critical · {s['WARNING']} warnings · {s['INFO']} info**",
    ]
    for sev in Severity:
        group = [f for f in findings if f.severity == sev]
        if not group:
            continue
        lines += [
            "",
            f"## {ICON[sev]} {sev.value.title()}",
            "",
            "| Category | Object | Detail |",
            "|---|---|---|",
        ]
        lines += [f"| {f.category} | `{f.object}` | {f.detail} |" for f in group]
    return "\n".join(lines) + "\n"
