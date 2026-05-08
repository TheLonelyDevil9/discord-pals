#!/usr/bin/env python3
"""Repository quality checks for agent-first maintenance."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_REQUIRED = (
    "docs/README.md",
    "docs/architecture.md",
    "docs/quality.md",
    "docs/agent-workflow.md",
    "docs/plans/README.md",
)
TRACKED_LARGE_MODULES = {
    "bot_instance.py": 3146,
    "dashboard.py": 3763,  # v2.2.10 updater recovery baseline
    "memory.py": 1938,
    "discord_utils.py": 1777,
}
LARGE_MODULE_GROWTH_BUDGET = 250


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _strip_heading(text: str) -> str:
    return re.sub(r"^# .+?\n+", "", text, count=1).strip()


def check_agent_instruction_parity(errors: list[str]) -> None:
    agents = _strip_heading(_read("AGENTS.md"))
    claude = _strip_heading(_read("CLAUDE.md"))
    if agents != claude:
        errors.append("AGENTS.md and CLAUDE.md must stay in parity except for their top heading.")


def check_docs_map(errors: list[str]) -> None:
    agents = _read("AGENTS.md")
    if len(agents.splitlines()) > 120:
        errors.append("AGENTS.md should stay short and point to docs instead of becoming a manual.")
    for doc_path in DOCS_REQUIRED:
        if not (ROOT / doc_path).exists():
            errors.append(f"Missing required repo knowledge doc: {doc_path}")
        elif doc_path not in agents and doc_path != "docs/README.md":
            errors.append(f"AGENTS.md should link to {doc_path}")


def _literal_dict_from_module(path: str, name: str) -> dict:
    tree = ast.parse(_read(path), filename=path)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"Could not find {name} in {path}")


def _config_field_keys() -> set[str]:
    tree = ast.parse(_read("runtime_config.py"), filename="runtime_config.py")
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CONFIG_FIELDS":
                    if not isinstance(node.value, ast.Dict):
                        raise AssertionError("CONFIG_FIELDS must be a dict literal")
                    keys = set()
                    for key in node.value.keys:
                        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                            raise AssertionError("CONFIG_FIELDS keys must be string literals")
                        keys.add(key.value)
                    return keys
    raise AssertionError("Could not find CONFIG_FIELDS in runtime_config.py")


def check_runtime_config_schema(errors: list[str]) -> None:
    defaults = _literal_dict_from_module("runtime_config.py", "DEFAULTS")
    field_keys = _config_field_keys()
    default_keys = set(defaults)
    missing = sorted(default_keys - field_keys)
    extra = sorted(field_keys - default_keys)
    if missing:
        errors.append(f"runtime_config.CONFIG_FIELDS missing defaults: {', '.join(missing)}")
    if extra:
        errors.append(f"runtime_config.CONFIG_FIELDS has unknown keys: {', '.join(extra)}")


def check_large_module_growth(errors: list[str]) -> None:
    for path, baseline in TRACKED_LARGE_MODULES.items():
        line_count = len(_read(path).splitlines())
        if line_count > baseline + LARGE_MODULE_GROWTH_BUDGET:
            errors.append(
                f"{path} grew to {line_count} lines; extract helpers or update docs with a new baseline."
            )


def main() -> int:
    errors: list[str] = []
    checks = (
        check_agent_instruction_parity,
        check_docs_map,
        check_runtime_config_schema,
        check_large_module_growth,
    )
    for check in checks:
        try:
            check(errors)
        except Exception as exc:
            errors.append(f"{check.__name__} failed: {exc}")

    if errors:
        print("Quality checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Quality checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
