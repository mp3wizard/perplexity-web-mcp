# Local update runbook

The cloud watcher (a scheduled Claude routine) only *detects* that upstream moved
— it runs `scripts/sync-upstream.sh --check` and stops. Applying happens here,
locally, because the security toolchain and the real `pwm` installation only
exist on this machine.

This file is the procedure, plus the findings baseline that makes the scan
results readable. Update it whenever a run teaches you something new.

## Procedure

```bash
bash scripts/sync-upstream.sh          # apply (no --check)
```

Exit codes: `0` applied or already current, `2` conflicts need resolving,
`1` hard failure with nothing committed.

Then, in order — do not skip to the push:

1. **Guard check.** `.chrome-debug-profile` absent, and the five divergent files
   below still hold this fork's side.
2. **Security scan** — see the next section for what to scan and what to ignore.
3. **Fix** what is mechanically fixable; report the rest rather than guessing.
4. **Install and test** — `uv tool install --force .` then
   `uv run --group tests pytest tests/ -q`.
5. **Push only if green.** Tests pass, no new critical/high unfixed, no
   credential trace.

## The five divergent files

Upstream will eventually touch these. Keep this fork's side in every conflicted
hunk, not just the first:

| File | Keep | Never accept |
|---|---|---|
| `pyproject.toml` | `fastmcp>=3.2.0,<4.0` | upstream's `<3.0` — restores GHSA-vv7q-7jx5-f767 (CVSS 10.0) |
| `tests/test_mcp_server.py` | the `tool_fn()` helper | upstream's `tool.fn` — absent in fastmcp 3.x |
| `tests/test_rate_limits.py` | the `tool_fn()` helper | upstream's `tool.fn` |
| `README.md` | the "About this fork" block | deletion of that block |
| `.gitignore` | the browser-profile entries | deletion of those entries |

## Scan the installed environment, not the dev environment

This is the trap. `uv sync` and `uv tool install` resolve **different dependency
sets**, and only one of them is what you actually run:

- `uv sync` installs from the committed `uv.lock`, pins and all. On 2026-08-20 that
  meant `cryptography==46.0.4` — CVSS 9.8 — along with the whole dev group
  (beautifulsoup4, lxml, ruff, pytest…). Scanning it reported **32 vulnerabilities
  across 13 packages**.
- `uv tool install .` ignores `uv.lock` and resolves fresh from `pyproject.toml`
  constraints. Same commit, same day: `cryptography==50.0.0`, no dev group, and
  **0 vulnerabilities across 73 packages**.

Neither number is wrong; they describe different things. The installed tool
environment is what `pwm` actually executes, so that is the one that gates the
push. Scan it:

```bash
uv tool install --force .
uv pip freeze --python ~/.local/share/uv/tools/perplexity-web-mcp-cli/bin/python \
  > /tmp/requirements-installed.txt
osv-scanner scan source -L /tmp/requirements-installed.txt
```

`osv-scanner` picks its extractor from the **filename**, so the file must be named
`requirements*.txt`. Named anything else it errors with "could not determine
extractor" rather than scanning nothing quietly.

A finding that appears only under `uv sync` is a note about the committed lockfile
— worth mentioning, not a push blocker.

## Secrets

Scan the upstream diff rather than full history — upstream's repo is ~156 MB
because of the committed browser profile, and `gitleaks detect` over the whole
thing does not finish in reasonable time.

```bash
mkdir -p /tmp/gl-scan
git diff <last-synced-sha>..<new-sha> -- . ':(exclude).chrome-debug-profile' \
  > /tmp/gl-scan/upstream-diff.patch
gitleaks detect --no-git --source /tmp/gl-scan -v
trufflehog filesystem /tmp/gl-scan --no-update
```

Point `--source` at a directory containing the patch, not at `/tmp` itself —
scanning `/tmp` reports "scanned ~0 bytes" and looks like a clean pass.

Upstream has committed live credentials before. Treat any hit as blocking.

## SAST baseline

`bandit -r src/` and `semgrep --config p/python src/` report the same findings
every run. These are **pre-existing** — do not fix them, do not let them block a
push, do not report them as new:

- `pwm api` binds `0.0.0.0` by default and `verify_auth()` accepts any request when
  `ANTHROPIC_API_KEY` is unset (`api/server.py`). Upstream design; mitigate at
  runtime with `--host 127.0.0.1` and a key. (Note: as of 0.14.10 upstream *did*
  add this guard on the MCP side — `is_loopback_host` plus a SystemExit — but not
  on the API side.)
- Wildcard CORS with credentials, `api/server.py` — the single recurring semgrep hit.
- bandit, ~36 findings in `src/`: B603 ×12 and B404 ×2 (subprocess in `cli/setup.py`
  and `cli/hack.py`, all `shutil.which()`-resolved binaries, never user input),
  B110 ×12 (best-effort try/except/pass in cleanup paths), B105 ×4 (strings like
  `"No token found"` matched as passwords), B104 ×3 (same bind-all root cause),
  B310, B607, B311 ×1 each.
- `mcps-audit` scores this repo 100/100 FAIL. Unreliable here: its two "CRITICAL
  injection" hits are f-string prompt assembly in `api/responses.py`, and its
  "hardcoded secret" is a string inside `__all__`.

**To tell new from pre-existing**, cross-reference finding line numbers against
the diff hunks — a total count alone will not do it. `scripts/sast-diff.py`
does this instead of doing it by hand:

```bash
uv run scripts/sast-diff.py <last-synced-sha> <new-sha>
```

Runs bandit and semgrep, diffs `<last>..<new>` for `src/`, and reports only the
findings whose line falls inside a hunk that diff actually touched. Exit `0`
means nothing new — the rest is the baseline above. Exit `1` prints the new
ones for review; they are not automatically bad, just not yet vetted.

Worked example, 0.14.10: bandit reported 36, but only 3 fell inside code the sync
touched — a subprocess call to a `shutil.which()`-resolved `codex` CLI in
`setup.py`, and two lockfile-cleanup `try/except/pass` blocks in `setup.py` and the
new daemon code in `mcp/server.py`. All three matched patterns already accepted
elsewhere in the codebase.

## Test baseline

Was 462. **487 as of 0.14.10** — upstream added tests for the new daemon
lifecycle. Bump this line when upstream adds more; a drop is a real signal.

Integration tests hit the live Perplexity API and skip without a session token.
Locally the token is present, so they run. They are not a push gate — a network
flake there is not a regression.

Also confirm the server still registers its tools:

```bash
uv run python -c "from perplexity_web_mcp.mcp.server import mcp; import asyncio; print(len(asyncio.run(mcp.list_tools())), 'tools')"
```

30 as of 0.14.10. Use `uv run` — a bare `python3` will not see the venv.
