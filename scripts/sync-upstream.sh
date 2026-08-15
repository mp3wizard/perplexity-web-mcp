#!/usr/bin/env bash
# sync-upstream.sh — pull new upstream commits into this fork, safely.
#
# Why this exists instead of `git merge upstream/main`:
#
#   1. This fork's history was rewritten (git filter-repo) to strip a committed
#      Chrome profile containing real credentials. That gave every commit a new
#      SHA, so this repo shares NO common ancestor with upstream. A plain merge
#      would need --allow-unrelated-histories and would conflict on every file.
#
#   2. Upstream still tracks .chrome-debug-profile/ (~358 MB, real Cookies and
#      Login Data). Any merge or pull would silently reintroduce it. This script
#      excludes that path and hard-fails if it ever slips through.
#
# So we sync by *content*, not history: diff upstream between the last synced
# commit and its current HEAD, then apply that patch here.
#
# Usage: bash scripts/sync-upstream.sh
# Exit:  0 = up to date or applied cleanly | 2 = conflicts need a human
#        1 = hard failure (never leaves the repo in a pushable state)

set -euo pipefail

UPSTREAM_URL="https://github.com/jacob-bd/perplexity-web-mcp.git"
FORBIDDEN_PATH=".chrome-debug-profile"
MARKER=".upstream-sync"

cd "$(git rev-parse --show-toplevel)"

[ -f "$MARKER" ] || { echo "FATAL: $MARKER missing — cannot determine last synced commit."; exit 1; }
LAST=$(tr -d '[:space:]' < "$MARKER")

git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM_URL"

# Cheap check first. Upstream is ~156 MB because of the committed browser
# profile, so a blind `git fetch` would pull that every single run. Ask GitHub
# for the head SHA instead (one API call) and only fetch when it actually moved.
NEW=$(gh api repos/jacob-bd/perplexity-web-mcp/commits/main --jq .sha 2>/dev/null \
      || git ls-remote "$UPSTREAM_URL" refs/heads/main | cut -f1)

[ -n "$NEW" ] || { echo "FATAL: could not read upstream HEAD."; exit 1; }

if [ "$LAST" = "$NEW" ]; then
  echo "UP_TO_DATE — upstream still at ${NEW:0:12}, nothing to sync."
  exit 0
fi

# Only now pay for the fetch, and skip blobs we do not need.
echo "Fetching upstream (blobless)..."
git fetch --quiet --filter=blob:none upstream main

echo "New upstream commits: ${LAST:0:12} -> ${NEW:0:12}"
git log --oneline "$LAST..$NEW" 2>/dev/null | sed 's/^/  /' || true

PATCH=$(mktemp)
trap 'rm -f "$PATCH"' EXIT

# Exclude the credential directory at the diff level, so it can never enter the
# working tree even transiently.
git diff "$LAST" "$NEW" -- . ":(exclude)$FORBIDDEN_PATH" > "$PATCH"

if [ ! -s "$PATCH" ]; then
  echo "Upstream moved but changed nothing outside $FORBIDDEN_PATH. Recording marker only."
  echo "$NEW" > "$MARKER"
  exit 0
fi

echo "Applying upstream patch (3-way)..."
STATUS=0
git apply --3way --whitespace=nowarn "$PATCH" || STATUS=$?

# Guard: the excluded path must never appear, patch applied or not.
if git status --porcelain | grep -q "$FORBIDDEN_PATH"; then
  echo "FATAL: $FORBIDDEN_PATH appeared in the working tree. Aborting, nothing committed."
  git checkout -- . 2>/dev/null || true
  git clean -fd "$FORBIDDEN_PATH" 2>/dev/null || true
  exit 1
fi

echo "$NEW" > "$MARKER"

if [ "$STATUS" -ne 0 ] || git diff --name-only --diff-filter=U | grep -q .; then
  echo
  echo "CONFLICTS — these files need manual resolution:"
  git diff --name-only --diff-filter=U | sed 's/^/  /'
  echo
  echo "Keep this fork's versions of these deliberate divergences:"
  echo "  pyproject.toml            -> fastmcp>=3.2.0,<4.0  (NOT upstream's <3.0; that pin carries a CVSS 10.0 advisory)"
  echo "  tests/test_mcp_server.py  -> tool_fn() helper      (NOT upstream's tool.fn; absent in fastmcp 3.x)"
  echo "  tests/test_rate_limits.py -> tool_fn() helper"
  echo "  README.md                 -> keep the 'About this fork' block"
  echo "  .gitignore                -> keep the browser-profile entries"
  exit 2
fi

echo "APPLIED_CLEAN — upstream synced to ${NEW:0:12}."
