"""Stop hook: nudges about implementation-verifier when backend has uncommitted changes."""

from __future__ import annotations

import shutil
import subprocess
import sys

REMINDER = (
    "[stop-reminder] backend/ has uncommitted changes. "
    "Consider running the implementation-verifier agent before declaring the task done."
)


def has_backend_changes() -> bool:
    git = shutil.which("git")
    if git is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603
            [git, "status", "--porcelain", "--", "backend/"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return bool(result.stdout.strip())


def main() -> int:
    if has_backend_changes():
        print(REMINDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
