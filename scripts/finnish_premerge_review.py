#!/usr/bin/env python3
"""Ask gpt-6.1-sol to review changed Finnish user-facing text before merging.

Collects the Finnish lines a branch adds to public and regular-user sources
(admin excluded; user emails only with --include-emails), builds a review
packet with the surrounding lines, placeholder examples and the site's
terminology rules, asks the model for exact replacements and writes a
Markdown report for the pull request. An unavailable model is reported as
not reviewed, never as passed. This is a routine, not a merge gate.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-6.1-sol"
CONTEXT_LINES = 3

SOURCE_SUFFIXES = (".html", ".htm", ".j2", ".jinja", ".jinja2", ".tsx", ".ts", ".jsx", ".js", ".py", ".go", ".po", ".txt")
SKIPPED_PREFIXES = (
    "tests/", "docs/", "changelog.d/", "scripts/", "deploy/", "frontend/tests/",
    "mielenosoitukset_fi/static/frontend/", "mielenosoitukset_fi/static/vendor/",
)
ADMIN_PATTERN = re.compile(r"(^|/)(admin|admin_V2|manuals)(/|$)|(^|/)(admin_base|manual_base)\.html$|GodScreen\.tsx$")
EMAIL_PATTERN = re.compile(
    r"(^|/)(emails|emailer)/|go-api/[^/]*(mail|email)[^/]*\.go$|deliver_[a-z_]+_confirmations\.py$"
)
FINNISH_CHARS = re.compile(r"[äöÄÖ]")
FINNISH_WORDS = re.compile(
    r"\b(ja|ei|tai|on|ovat|voit|sinun|olet|että|kun|jos|sekä|kanssa|tämä|tämän|"
    r"mielenosoitus\w*|ilmoittau\w*|kirjaudu\w*|lähetä\w*|tallenna\w*|peruuta\w*|"
    r"sähköpost\w*|tiedot|virhe\w*|yritä|uudelleen|hae|haku|sivu\w*|takaisin)\b",
    re.IGNORECASE,
)
PLACEHOLDER = re.compile(r"\$\{([^}]+)\}|%\((\w+)\)[sd]|\{\{\s*([^}]+?)\s*\}\}|\{(\w+)\}|%[sd]")
COUNT_NAME = re.compile(r"count|^n$|num|total|maara|määrä|lkm|amount|participants", re.IGNORECASE)
PLURAL_HINT = re.compile(r"ngettext|_n\(|plural|=== ?1\b|== ?1\b|!== ?1\b|count\s*[<>=!]", re.IGNORECASE)


@dataclass
class Change:
    path: str
    line: int
    text: str
    removed: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    plural_nearby: bool = False


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def admin_paths_from_inventory() -> set[str]:
    status = ROOT / "docs/finnish-copy-status.csv"
    if not status.is_file():
        return set()
    with status.open(newline="") as source:
        return {row["path"] for row in csv.DictReader(source) if row["evidence"].startswith("finnish-admin")}


def classify(path: str, admin_inventory: set[str]) -> str:
    if not path.endswith(SOURCE_SUFFIXES) or path.startswith(SKIPPED_PREFIXES):
        return "out of scope"
    if "/translations/" in path and "/fi/" not in path:
        return "out of scope"
    if path.endswith(("_test.go", ".test.ts", ".test.tsx")):
        return "out of scope"
    if path in admin_inventory or ADMIN_PATTERN.search(path):
        return "admin"
    if EMAIL_PATTERN.search(path):
        return "email"
    return "ui"


def looks_finnish(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped.startswith(("import ", "from ", "//", "#", "/*", "*")) and not FINNISH_CHARS.search(stripped):
        return False
    return bool(FINNISH_CHARS.search(stripped) or len(FINNISH_WORDS.findall(stripped)) >= 2)


def example_value(name: str, count: int) -> str:
    if COUNT_NAME.search(name):
        return str(count)
    return {"city": "Helsinki", "kaupunki": "Helsinki", "date": "16.11.2026", "time": "klo 18.00"}.get(
        name.strip().split(".")[-1].split("|")[0].strip().lower(), "Esimerkki"
    )


def placeholder_examples(text: str) -> tuple[list[str], list[str]]:
    names = []
    for match in PLACEHOLDER.finditer(text):
        names.append(next((group for group in match.groups() if group), match.group(0)))
    if not names:
        return [], []
    counts = (1, 2, 5) if any(COUNT_NAME.search(name) for name in names) else (1,)
    examples = []
    for count in counts:
        index = iter(names)
        examples.append(PLACEHOLDER.sub(lambda _m: example_value(next(index), count), text.strip()))
    return names, examples


def parse_diff(diff: str) -> dict[str, list[tuple[int, str, list[str]]]]:
    """Return added lines per file, each with the removed text it replaces.

    A block of removed lines followed by the same number of added lines is
    paired line by line; otherwise each added line gets the whole block.
    """
    files: dict[str, list[tuple[int, str, list[str]]]] = {}
    path, new_line, removed, added = None, 0, [], []

    def flush() -> None:
        paired = len(removed) == len(added)
        for index, (line, text) in enumerate(added):
            files.setdefault(path, []).append((line, text, [removed[index]] if paired else list(removed)))
        removed.clear()
        added.clear()

    for raw in diff.splitlines():
        if raw.startswith(("diff --git", "--- ", "+++ ")) or re.match(r"@@ ", raw):
            flush()
            if raw.startswith("+++ "):
                path = raw[6:] if raw.startswith("+++ b/") else None
            hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if hunk:
                new_line = int(hunk.group(1))
            continue
        if path is None:
            continue
        if raw.startswith("-"):
            if added:
                flush()
            removed.append(raw[1:])
        elif raw.startswith("+"):
            added.append((new_line, raw[1:]))
            new_line += 1
        elif not raw.startswith("\\"):
            flush()
            new_line += 1
    flush()
    return files


def collect(base: str, include_emails: bool) -> tuple[list[Change], dict[str, list[str]]]:
    diff = git("diff", "--unified=0", "--no-color", f"{base}...HEAD")
    admin_inventory = admin_paths_from_inventory()
    skipped: dict[str, list[str]] = {"admin": [], "email": []}
    changes = []
    for path, added in sorted(parse_diff(diff).items()):
        scope = classify(path, admin_inventory)
        if scope == "out of scope":
            continue
        finnish = [(line, text, removed) for line, text, removed in added if looks_finnish(text)]
        if not finnish:
            continue
        if scope == "admin" or (scope == "email" and not include_emails):
            skipped[scope].append(path)
            continue
        source = (ROOT / path).read_text(errors="replace").splitlines()
        for line, text, removed in finnish:
            start, end = max(0, line - 1 - CONTEXT_LINES), min(len(source), line + CONTEXT_LINES)
            names, examples = placeholder_examples(text)
            nearby = "\n".join(source[max(0, line - 8):line + 7])
            changes.append(Change(
                path=path, line=line, text=text.strip(),
                removed=[r.strip() for r in removed if looks_finnish(r)],
                context=[f"{n + 1}: {source[n]}" for n in range(start, end)],
                placeholders=names, examples=examples,
                plural_nearby=bool(PLURAL_HINT.search(nearby)),
            ))
    return changes, skipped


def build_prompt(changes: list[Change]) -> str:
    style = (ROOT / "docs/finnish-style.md").read_text()
    items = []
    for number, change in enumerate(changes, 1):
        block = [f"### F{number}: {change.path}:{change.line}", f"Changed line: {change.text}"]
        if change.removed:
            block.append("Replaced text: " + " / ".join(change.removed))
        block.append("Surrounding source:\n```\n" + "\n".join(change.context) + "\n```")
        if change.examples:
            block.append("Placeholders: " + ", ".join(change.placeholders))
            block.append("Rendered with sample values:\n" + "\n".join(f"- {e}" for e in change.examples))
        if change.plural_nearby:
            block.append("Count-dependent wording nearby: check singular and plural forms.")
        items.append("\n".join(block))
    return f"""You review Finnish user-facing text for Mielenosoitukset.fi before a pull request merges.

Rules: follow the style guide below (tone, structures, punctuation, established terms and
what must not change) and standard Finnish (Kielitoimiston ohjepankki). Review only the
changed lines F1..F{len(changes)}; use the surrounding source for the whole rendered sentence.
Check grammar, case and agreement with every sample rendering, including singular and
plural. Do not propose a change only for taste when the text is correct and clear. Keep
facts, dates, permit numbers, placeholders, markup and code identifiers unchanged.

Answer with JSON only, in this shape:
{{"findings": [{{"id": "F1", "current": "exact current text", "replacement": "exact replacement",
"kind": "error" or "proposal", "reason": "short reason in English"}}]}}
Use an empty list when nothing needs changing. "current" must be copied exactly from the changed line.

## Style guide (docs/finnish-style.md)

{style}

## Changed lines

""" + "\n\n".join(items) + "\n"


def default_command() -> list[str] | None:
    configured = os.environ.get("FINNISH_REVIEW_COMMAND")
    if configured:
        return shlex.split(configured)
    if shutil.which("codex"):
        return ["codex", "exec", "--model", MODEL, "--sandbox", "read-only", "-"]
    return None


def ask_model(prompt: str, command: list[str] | None, timeout: int) -> tuple[str, str, list[dict]]:
    """Return (availability, detail, findings). Availability is AVAILABLE or UNAVAILABLE."""
    if command is None:
        return "UNAVAILABLE", "no model command: install the codex CLI or set FINNISH_REVIEW_COMMAND", []
    try:
        result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "UNAVAILABLE", f"{type(error).__name__}: {error}", []
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        return "UNAVAILABLE", f"exit {result.returncode}: {detail[0][:300]}", []
    match = re.search(r"\{.*\"findings\".*\}", result.stdout, re.DOTALL)
    if not match:
        return "UNAVAILABLE", "the model answered without the requested JSON", []
    try:
        findings = json.loads(match.group(0))["findings"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        return "UNAVAILABLE", f"unreadable model answer: {error}", []
    return "AVAILABLE", " ".join(command[:4]), findings


def cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def report(changes: list[Change], skipped: dict[str, list[str]], availability: str, detail: str,
           findings: list[dict], include_emails: bool, head: str) -> tuple[str, str]:
    if not changes:
        outcome = "NO CHANGED FINNISH TEXT"
    elif availability != "AVAILABLE":
        outcome = "NOT REVIEWED"
    elif findings:
        outcome = "UNRESOLVED FINDINGS"
    else:
        outcome = "NO FINDINGS"
    lines = [
        "## Finnish wording review",
        "",
        f"Result: **{outcome}**. Commit `{head[:12]}`. Advisory routine, not a merge gate.",
        f"Model: `{MODEL}`, {availability.lower() if changes else 'not needed'}"
        + (f" ({detail})." if changes else "."),
        f"Changed Finnish lines found: {len(changes)}. User emails: "
        + ("included." if include_emails else "excluded until the FI-COPY-01 UI pass is done."),
    ]
    for scope, label in (("admin", "Admin sources excluded"), ("email", "Email sources not reviewed")):
        if skipped[scope]:
            lines.append(f"{label}: " + ", ".join(f"`{p}`" for p in skipped[scope]) + ".")
    if outcome == "NOT REVIEWED":
        lines += ["", "The model could not review this change, so the check has not passed. "
                  "Run the review again when the model is available, or record a human review."]
    if findings:
        by_id = {f"F{n}": change for n, change in enumerate(changes, 1)}
        lines += ["", "| ID | Source | Current | Replacement | Kind | Reason | Status |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for finding in findings:
            change = by_id.get(str(finding.get("id")))
            where = f"`{change.path}:{change.line}`" if change else cell(finding.get("id", "?"))
            lines.append("| " + " | ".join([
                cell(finding.get("id", "?")), where, cell(finding.get("current", "")),
                cell(finding.get("replacement", "")), cell(finding.get("kind", "")),
                cell(finding.get("reason", "")), "unresolved",
            ]) + " |")
    return outcome, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main", help="branch the PR merges into (default origin/main)")
    parser.add_argument("--include-emails", action="store_true", help="also review user email sources")
    parser.add_argument("--packet", type=Path, help="write the model prompt here instead of asking the model")
    parser.add_argument("--report", type=Path, help="write the Markdown report here as well as to stdout")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    changes, skipped = collect(args.base, args.include_emails)
    head = git("rev-parse", "HEAD").strip()
    prompt = build_prompt(changes) if changes else ""
    if args.packet:
        args.packet.write_text(prompt)
        print(f"{len(changes)} changed Finnish lines; prompt written to {args.packet}", file=sys.stderr)
        return 0
    availability, detail, findings = ("AVAILABLE", "", []) if not changes else ask_model(prompt, default_command(), args.timeout)
    outcome, text = report(changes, skipped, availability, detail, findings, args.include_emails, head)
    if args.report:
        args.report.write_text(text)
    print(text, end="")
    return {"NO CHANGED FINNISH TEXT": 0, "NO FINDINGS": 0, "UNRESOLVED FINDINGS": 1}.get(outcome, 2)


if __name__ == "__main__":
    raise SystemExit(main())
