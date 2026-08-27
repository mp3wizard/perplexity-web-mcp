#!/usr/bin/env python3
"""Split a bandit/semgrep run into new-vs-pre-existing findings.

bandit and semgrep report the whole tree every time, so the same ~35 findings
show up on every sync regardless of what changed. Manually cross-referencing
each finding's line number against `git diff --unified=0`'s hunk headers is
the actual signal, and doing it by hand each sync is what this replaces.

Usage:
    uv run scripts/sast-diff.py <old-sha> <new-sha>

Exit: 0 if no finding falls inside a changed hunk, 1 if any does (push gate).
Runs bandit and semgrep itself; needs both on PATH.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys


def changed_ranges(old: str, new: str, path: str) -> dict[str, list[tuple[int, int]]]:
    """file -> list of (start, end) line ranges touched in the new tree."""
    diff = subprocess.run(
        ["git", "diff", "--unified=0", f"{old}..{new}", "--", path],
        capture_output=True, text=True, check=True,
    ).stdout

    ranges: dict[str, list[tuple[int, int]]] = {}
    current_file = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@") and current_file:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) else 1
            if count == 0:  # pure deletion, nothing added in the new file
                continue
            ranges.setdefault(current_file, []).append((start, start + count - 1))
    return ranges


def touches(ranges: dict[str, list[tuple[int, int]]], file: str, line: int) -> bool:
    for f, spans in ranges.items():
        if file.endswith(f):
            return any(a <= line <= b for a, b in spans)
    return False


def run_bandit() -> list[dict]:
    out = subprocess.run(
        ["bandit", "-r", "src/", "-f", "json", "-q"],
        capture_output=True, text=True,
    ).stdout
    return json.loads(out).get("results", []) if out.strip() else []


def run_semgrep() -> list[dict]:
    out = subprocess.run(
        ["semgrep", "--config", "p/python", "src/", "--json", "-q"],
        capture_output=True, text=True,
    ).stdout
    return json.loads(out).get("results", []) if out.strip() else []


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <old-sha> <new-sha>", file=sys.stderr)
        return 2
    old, new = sys.argv[1], sys.argv[2]

    ranges = changed_ranges(old, new, "src/")

    bandit_findings = run_bandit()
    semgrep_findings = run_semgrep()

    new_bandit = [
        r for r in bandit_findings
        if touches(ranges, r["filename"], r["line_number"])
    ]
    new_semgrep = [
        r for r in semgrep_findings
        if touches(ranges, r["path"], r["start"]["line"])
    ]

    print(f"bandit:  {len(bandit_findings)} total, {len(new_bandit)} in changed code")
    print(f"semgrep: {len(semgrep_findings)} total, {len(new_semgrep)} in changed code")

    if new_bandit or new_semgrep:
        print("\nNEW findings (in code this sync touched) — review before push:")
        for r in new_bandit:
            print(f"  bandit  {r['test_id']:6} {r['filename']}:{r['line_number']}  {r['issue_text'][:70]}")
        for r in new_semgrep:
            print(f"  semgrep {r['check_id']} {r['path']}:{r['start']['line']}")
        return 1

    print("\nNo findings in changed code — remainder is pre-existing baseline (see docs/local-update.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
