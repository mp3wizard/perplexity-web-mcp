"""Repository hygiene checks for local-only runtime artifacts."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ".chrome-debug-profile"
PROFILE_SAMPLE = f"{PROFILE_DIR}/Default/Cookies"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_chrome_debug_profile_is_untracked_and_ignored() -> None:
    tracked = _git("ls-files", "--", PROFILE_DIR)
    assert tracked.returncode == 0
    assert not tracked.stdout, f"{len(tracked.stdout.splitlines())} profile paths are still tracked"

    ignored = _git("check-ignore", "--no-index", "-v", PROFILE_SAMPLE)
    assert ignored.returncode == 0, f"{PROFILE_SAMPLE} is not covered by .gitignore"
    source = ignored.stdout.split(":", 1)[0]
    assert source == ".gitignore", f"{PROFILE_SAMPLE} is ignored by {source!r}, not the repository .gitignore"
