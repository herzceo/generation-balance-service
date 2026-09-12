"""Stop hook: self-reflection prompt for memory and knowledge maintenance.

On first stop (stop_hook_active=False), exits 2 with a structured reflection
prompt. On second stop (stop_hook_active=True), exits 0.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


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


def memory_dir() -> Path:
    escaped = str(Path.cwd()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / escaped / "memory"


def build_prompt() -> str:
    mem_dir = memory_dir()
    arch_note = ""
    if has_backend_changes():
        arch_note = (
            "\n\n[architecture] backend/ has uncommitted changes. "
            "If structural layers were modified (new entities, ports, handlers, "
            "or infra adapters), consider running the architecture-reviewer agent "
            "to verify import direction compliance before closing."
        )

    return (
        "[stop-reflection] Before this session ends, review what happened:\n\n"
        "1. DESIGN DECISIONS: Were any architectural decisions made this session "
        "(e.g. which pattern to use, how to model a domain concept, "
        "API shape choices, trade-off resolutions)?\n\n"
        "2. FEEDBACK CORRECTIONS: Did the user correct an approach, reject a "
        "pattern, or ask you to do something differently than you assumed? "
        "These corrections should become memory entries so the mistake is not repeated.\n\n"
        "3. NEW PATTERNS: Was a new convention established or an existing rule "
        "clarified (naming, structure, error handling, test approach)?\n\n"
        "4. CLAUDE.MD / RULES UPDATES: Should any `.claude/rules/*.md` file or "
        "CLAUDE.md be updated to reflect what was learned?\n\n"
        "IF any of the above apply, propose the specific memory file(s) or rule "
        f"update(s) now. Memory files go in:\n  {mem_dir}/\n"
        "Use the format of existing memory files (YAML front matter: name, "
        "description, type). Wait for user confirmation before writing any files.\n\n"
        "IF nothing notable happened this session, respond with exactly: "
        "'[reflection] Nothing to save this session.' and stop."
        f"{arch_note}"
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}

    if payload.get("stop_hook_active"):
        return 0

    if not has_backend_changes():
        return 0

    print(build_prompt())
    return 2


if __name__ == "__main__":
    sys.exit(main())
