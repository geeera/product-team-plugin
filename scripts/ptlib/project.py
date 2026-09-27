"""Minimal readers for .product-team/project.yml (stdlib only: no YAML parser is guaranteed in cloud sessions)."""
from __future__ import annotations

import re

PROJECT_FILE = ".product-team/project.yml"


def plugin_ref(path: str = PROJECT_FILE) -> str:
    """The release channel or tag the product follows (`team.plugin_ref`), `stable` by default."""
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r"^\s*plugin_ref:\s*['\"]?([\w./-]+)", f.read(), re.MULTILINE)
    except FileNotFoundError:
        return "stable"
    return m.group(1) if m else "stable"


def freeze_days(path: str = PROJECT_FILE) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r"^\s*freeze_days:\s*(\d+)", f.read(), re.MULTILINE)
    except FileNotFoundError:
        return 2
    return int(m.group(1)) if m else 2
