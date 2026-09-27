"""GitHub access for the plugin scripts: the REST API over HTTPS with the session's token, no `gh` CLI needed.

Cloud sessions have a token in the environment but not always the `gh` binary, so this module talks to the API
directly. Token lookup: GH_TOKEN, GITHUB_TOKEN, then `gh auth token` when the CLI happens to exist. With no
token at all requests go out unauthenticated, which still works where a proxy adds credentials.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, List, Optional, Tuple

API = os.environ.get("PT_GITHUB_API", "https://api.github.com").rstrip("/")
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
_REMOTE = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
_token_cache: Optional[str] = None


class GhError(RuntimeError):
    pass


def token() -> str:
    global _token_cache
    if _token_cache is None:
        _token_cache = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if not _token_cache and shutil.which("gh"):
            proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
            _token_cache = proc.stdout.strip() if proc.returncode == 0 else ""
    return _token_cache


def _request(method: str, url: str, body: Optional[dict] = None, accept: str = "application/vnd.github+json") -> Tuple[str, dict]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "product-team-plugin"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise GhError(f"{method} {url} → HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GhError(f"{method} {url} failed: {exc.reason}") from exc


def _url(path: str) -> str:
    return path if path.startswith("http") else f"{API}/{path.lstrip('/')}"


def api(path: str, method: str = "GET", fields: Optional[dict] = None) -> Any:
    text, _ = _request(method, _url(path), fields)
    return json.loads(text) if text.strip() else None


def api_list(path: str) -> List[Any]:
    """Every item of a paginated list endpoint (follows Link: rel="next")."""
    items: List[Any] = []
    url: Optional[str] = _url(path)
    while url:
        text, headers = _request("GET", url)
        page = json.loads(text) if text.strip() else []
        # Some list endpoints wrap the array, e.g. check-runs → {"total_count", "check_runs": [...]}.
        if isinstance(page, dict):
            page = next((v for v in page.values() if isinstance(v, list)), [])
        items.extend(page)
        match = _NEXT.search(headers.get("Link") or headers.get("link") or "")
        url = match.group(1) if match else None
    return items


def raw(path: str, accept: str) -> str:
    """A non-JSON representation, e.g. a pull request diff."""
    text, _ = _request("GET", _url(path), accept=accept)
    return text


def graphql(query: str, variables: dict) -> Any:
    result = api("graphql", "POST", {"query": query, "variables": variables})
    if result and result.get("errors"):
        raise GhError(f"GraphQL: {result['errors']}")
    return (result or {}).get("data")


def parse_remote(url: str) -> Optional[str]:
    """owner/repo from a GitHub remote URL, including proxied ones that keep the github.com/owner/repo tail."""
    m = _REMOTE.search(url.strip())
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    parts = [p for p in re.split(r"[/:]", url.strip().rstrip("/")) if p]
    if len(parts) < 2:
        return None
    name = parts[-1][:-4] if parts[-1].endswith(".git") else parts[-1]
    return f"{parts[-2]}/{name}"


def repo() -> str:
    """owner/repo: PT_REPO, else `repo:` in .product-team/project.yml, else the origin remote."""
    explicit = os.environ.get("PT_REPO")
    if explicit:
        return explicit
    try:
        with open(".product-team/project.yml", encoding="utf-8") as f:
            m = re.search(r"^repo:\s*['\"]?([\w.-]+/[\w.-]+)", f.read(), re.MULTILINE)
        if m:
            return m.group(1)
    except FileNotFoundError:
        pass
    proc = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=False)
    found = parse_remote(proc.stdout) if proc.returncode == 0 else None
    if not found:
        raise GhError("cannot tell the repository: set PT_REPO=owner/repo or `repo:` in .product-team/project.yml")
    return found


def owner_login(repo_name: str) -> str:
    return api(f"repos/{repo_name}")["owner"]["login"]
