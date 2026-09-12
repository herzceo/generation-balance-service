"""Notification hook: shows a macOS desktop notification on Claude Code events."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys

TITLE = "Claude Code"
DEFAULT_MESSAGE = "Claude Code is waiting"
MAX_MESSAGE_LEN = 200


def show_macos_notification(message: str) -> None:
    osascript = shutil.which("osascript")
    if osascript is None:
        return
    safe = message.replace("\\", "\\\\").replace('"', '\\"')[:MAX_MESSAGE_LEN]
    script = f'display notification "{safe}" with title "{TITLE}" sound name "Glass"'
    try:
        subprocess.run(  # noqa: S603
            [osascript, "-e", script],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return


def main() -> int:
    if platform.system() != "Darwin":
        return 0
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    raw = payload.get("message") or DEFAULT_MESSAGE
    message = str(raw).strip() or DEFAULT_MESSAGE
    show_macos_notification(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
