"""Guard for layer dependency rules.

Two modes:
  - Hook mode (stdin JSON): checks a single edit from Claude Code PreToolUse
  - Scan mode (no stdin):   checks all backend/**/*.py files

Layer dependency rules:
  domain/   -> only: domain/, internal/
  app/      -> only: app/, domain/, internal/
  entry/    -> anything in backend.*
  infra/    -> infra/, domain/, app.shared (ports only), internal/
  internal/ -> only: internal/

Exits 2 on violations.
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

VIOLATION_EXIT_CODE = 2
MIN_IMPORT_PARTS = 2

ALLOWED_IMPORTS: dict[str, tuple[str, ...]] = {
    "domain": ("domain", "internal"),
    "app": ("app", "domain", "internal"),
    "entry": ("entry", "app", "domain", "infra", "internal", "main"),
    "infra": ("infra", "domain", "internal"),
    "internal": ("internal",),
    "main": ("main", "entry", "app", "domain", "infra", "internal"),
}

INFRA_APP_ALLOWLIST = re.compile(r"^backend\.app\.shared\b")

IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+(backend\.[\w.]+)",
    re.MULTILINE,
)


def source_layer(rel_path: str) -> str | None:
    parts = rel_path.split("/")
    if len(parts) < MIN_IMPORT_PARTS or parts[0] != "backend":
        return None
    return parts[1]


def extract_new_content(tool_name: str, tool_input: dict[str, object]) -> str:
    if tool_name == "Write":
        return str(tool_input.get("content") or "")
    if tool_name == "Edit":
        return str(tool_input.get("new_string") or "")
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return ""
        return "\n".join(str(e.get("new_string") or "") for e in edits if isinstance(e, dict))
    return ""


def find_violations(src_layer: str, content: str) -> list[tuple[str, str]]:
    allowed = ALLOWED_IMPORTS.get(src_layer)
    if allowed is None:
        return []

    violations: list[tuple[str, str]] = []
    for match in IMPORT_RE.finditer(content):
        imported = match.group(1)
        parts = imported.split(".")
        if len(parts) < MIN_IMPORT_PARTS:
            continue
        target_layer = parts[1]

        if target_layer in allowed:
            continue

        if src_layer == "infra" and target_layer == "app" and INFRA_APP_ALLOWLIST.match(imported):
            continue

        violations.append((imported, target_layer))
    return violations


def format_violations(rel: str, src_layer: str, violations: list[tuple[str, str]]) -> str:
    lines = [
        f"[guard_layers] layer violation in {rel} (layer: {src_layer})",
        "  forbidden imports:",
    ]
    for imp, target in violations:
        lines.append(f"    - {imp}  (-> forbidden layer: {target})")
    lines.append(
        f"  allowed imports for {src_layer}/: {', '.join(ALLOWED_IMPORTS.get(src_layer, ()))}"
    )
    if src_layer == "infra":
        lines.append("  note: infra/ may import from backend.app.shared.* (ports only)")
    return "\n".join(lines)


def _parse_hook_payload() -> tuple[str, str, str] | None:
    """Parse stdin JSON and return (rel_path, tool_name, content) or None to skip."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return None

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None

    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path or not str(file_path).endswith(".py"):
        return None

    cwd = payload.get("cwd") or ""
    try:
        rel = str(Path(str(file_path)).resolve().relative_to(Path(str(cwd)).resolve()))
    except (ValueError, OSError):
        return None

    content = extract_new_content(str(tool_name), tool_input)
    if not content:
        return None

    return rel, tool_name, content


def hook_mode() -> int:
    """Check a single edit from Claude Code PreToolUse stdin JSON."""
    parsed = _parse_hook_payload()
    if parsed is None:
        return 0

    rel, _, content = parsed
    src_layer = source_layer(rel)
    if src_layer is None:
        return 0

    violations = find_violations(src_layer, content)
    if not violations:
        return 0

    sys.stderr.write(format_violations(rel, src_layer, violations) + "\n")
    return VIOLATION_EXIT_CODE


def scan_mode() -> int:
    """Scan all backend/**/*.py files for layer violations."""
    root = Path("backend")
    if not root.is_dir():
        return 0

    all_violations: list[str] = []
    for py_file in sorted(root.rglob("*.py")):
        rel = str(py_file)
        src_layer = source_layer(rel)
        if src_layer is None:
            continue

        content = py_file.read_text()
        violations = find_violations(src_layer, content)
        if violations:
            all_violations.append(format_violations(rel, src_layer, violations))

    if all_violations:
        sys.stderr.write("\n\n".join(all_violations) + "\n")
        return VIOLATION_EXIT_CODE
    return 0


def main() -> int:
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        if raw.strip():
            sys.stdin = io.StringIO(raw)
            return hook_mode()
    return scan_mode()


if __name__ == "__main__":
    sys.exit(main())
