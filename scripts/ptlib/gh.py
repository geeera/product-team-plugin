"""Thin wrapper over the GitHub CLI; the only module that shells out to `gh`."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Optional, Sequence


class GhError(RuntimeError):
    pass


def run(args: Sequence[str], stdin: Optional[str] = None) -> str:
    try:
        proc = subprocess.run(
            ["gh", *args], input=stdin, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise GhError("GitHub CLI `gh` is not installed or not on PATH") from exc
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def run_json(args: Sequence[str], stdin: Optional[str] = None) -> Any:
    out = run(args, stdin)
    return json.loads(out) if out.strip() else None


def api(path: str, method: str = "GET", fields: Optional[dict] = None) -> Any:
    args = ["api", path, "--method", method]
    stdin = None
    if fields is not None:
        args += ["--input", "-"]
        stdin = json.dumps(fields)
    return run_json(args, stdin)


def api_list(path: str) -> list:
    """All items of a paginated list endpoint; one JSON object per line keeps pages mergeable."""
    out = run(["api", path, "--paginate", "--jq", ".[]"])
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def repo() -> str:
    """owner/repo from PT_REPO, else the current checkout."""
    explicit = os.environ.get("PT_REPO")
    if explicit:
        return explicit
    data = run_json(["repo", "view", "--json", "nameWithOwner"])
    return data["nameWithOwner"]


def owner_login(repo_name: str) -> str:
    return api(f"repos/{repo_name}")["owner"]["login"]
