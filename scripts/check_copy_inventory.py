#!/usr/bin/env python3
"""Check that the Finnish copy status CSV covers every tracked UI source."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"REVIEWED", "REVIEWED / USAGE TODO", "NO OWN COPY", "PENDING", "EMAIL PENDING"}


def main() -> int:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT, text=True
    ).split("\0")
    expected = {
        path for path in tracked
        if path.endswith((".html", ".htm", ".j2", ".jinja", ".jinja2", ".jsx", ".tsx"))
        or path.startswith("frontend/src/") and path.endswith(".ts")
    }
    with (ROOT / "docs/finnish-copy-status.csv").open(newline="") as source:
        rows = list(csv.DictReader(source))
    counts = Counter(row["path"] for row in rows)
    issues = []
    for label, paths in (
        ("Missing", expected - counts.keys()),
        ("Extra", counts.keys() - expected),
        ("Duplicate", {path for path, count in counts.items() if count != 1}),
        ("Invalid status", {row["path"] for row in rows if row["status"] not in STATUSES}),
        ("Missing audit evidence", {
            row["path"] for row in rows
            if not (ROOT / "docs" / row["evidence"]).is_file()
        }),
    ):
        if paths:
            issues.append(f"{label}: " + ", ".join(sorted(paths)))
    # The human-readable index must list the same complete set.
    markdown = (ROOT / "docs/finnish-copy-status.md").read_text()
    missing_links = {path for path in expected if f"[{path}]" not in markdown}
    if missing_links:
        issues.append("Missing Markdown rows: " + ", ".join(sorted(missing_links)))
    if issues:
        print("\n".join(issues))
        return 1
    print(f"PASS: {len(expected)} tracked UI sources; no missing or duplicate entries.")
    for status, count in sorted(Counter(row["status"] for row in rows).items()):
        print(f"{status}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
