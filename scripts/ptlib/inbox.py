"""The owner's single to-do list: on GitHub one pinned issue rewritten from the backlog every run; on the team
console its `GET …/inbox` read model (ADR 0006 decision 12), rendered the same way for `inbox show` and the digest."""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

from .transport import is_loopback

MARKER = "<!-- pt-inbox -->"
# Markdown control characters that could close/open a construct (a link, emphasis, code span) inside
# console-supplied text; a literal backslash is escaped first so re.sub never double-escapes it.
_MARKDOWN_CONTROL = re.compile(r"[\\\[\]()<>*_`]")

# key -> (title, the one action the owner takes), per owner language
TEXT: Dict[str, Dict[str, tuple]] = {
    "en": {
        "setup": ("Security setup", "Until this is done, agents can imitate your approvals"),
        "paused": ("Team is paused", "Check the failed runs, then comment `/resume` on the run log"),
        "release": ("Release decision", "On the demo issue: `/go` or `/no-go why`"),
        "design": ("Designs to approve", "Open the link, then `/approve` or `/reject why` on the issue"),
        "question": ("Questions", "Each line says what to answer"),
        "owner": ("Only you can do", "Payment, account or decision described on the issue"),
        "local": ("Needs your machine", "Run locally (e.g. on a Mac); the issue says what"),
        "fyi": ("Decided by the team (FYI)", "Nothing to do; `/reject why` on the issue reverses it"),
        "count": ("{n} thing needs you.", "{n} things need you."),
        "updated": "Updated {when} by the product team; rewritten every run.",
        "none": "Nothing needs you right now.",
    },
    "ru": {
        "setup": ("Безопасность", "Пока это не сделано, агенты могут изобразить твоё одобрение"),
        "paused": ("Команда на паузе", "Посмотри упавшие запуски и напиши `/resume` в журнале запусков"),
        "release": ("Решение о релизе", "В issue демо: `/go` или `/no-go причина`"),
        "design": ("Дизайн на утверждение", "Открой ссылку, затем `/approve` или `/reject причина` в issue"),
        "question": ("Вопросы", "В каждой строке написано, что ответить"),
        "owner": ("Только ты можешь", "Оплата, аккаунт или решение — описано в issue"),
        "local": ("Нужен твой компьютер", "Запустить локально (например, на Mac); что именно — в issue"),
        "fyi": ("Решено командой (к сведению)", "Делать ничего не нужно; `/reject причина` в issue отменит"),
        "count": ("Нужно твоё внимание: {n}.", "Нужно твоё внимание: {n}."),
        "updated": "Обновлено {when}; переписывается каждым запуском.",
        "none": "Сейчас от тебя ничего не нужно.",
    },
}
ORDER = ("setup", "paused", "release", "design", "question", "owner", "local")
# On the console the owner answers on the item's page, not with a GitHub comment.
CONSOLE_ACTIONS: Dict[str, Dict[str, str]] = {
    "en": {
        "release": "On the demo item in the console: go or no-go",
        "design": "Open the design, then approve or reject it on the item in the console",
        "question": "Answer each one on its item in the console",
    },
    "ru": {
        "release": "В консоли, на пункте демо: go или no-go",
        "design": "Открой дизайн, затем одобри или отклони его на пункте в консоли",
        "question": "Ответь на каждый на его пункте в консоли",
    },
}
CONSOLE_SECTIONS = ("release", "design", "question", "owner", "local")  # the console's `Section`
SETUP_TITLE = "Agents use your GitHub account — give them their own GitHub Apps (reference/identities.md)"


def classify(issue: dict):
    labels = set(issue.get("labels") or [])
    if "team:demo" in labels:
        return "release"
    if "design:awaiting-approval" in labels:
        return "design"
    if "needs:owner" in labels and issue.get("kind") != "question":
        return "owner"
    if issue.get("kind") == "question":
        return "question"
    if "needs:local" in labels:
        return "local"
    return None


def sections(issues: List[dict], paused_url: str = "", same_account_url: str = "", decided: List[dict] = ()) -> Dict[str, list]:
    groups: Dict[str, list] = {key: [] for key in ORDER + ("fyi",)}
    if same_account_url:
        groups["setup"].append({"title": SETUP_TITLE, "url": same_account_url})
    if paused_url:
        groups["paused"].append({"title": "Run log", "url": paused_url})
    for issue in sorted(issues, key=lambda i: i["number"]):
        key = classify(issue)
        if key:
            groups[key].append(issue)
    groups["fyi"] = list(decided)[:5]
    return groups


def _text(dto: Dict[str, Any], name: str) -> Optional[str]:
    value = dto.get(name)
    return value if isinstance(value, str) and value else None


def console_groups(dto: Dict[str, Any], item_url: Callable[[int], Optional[str]]) -> Dict[str, list]:
    """The console's InboxDto as `sections()` groups, in the console's order. Its rows are data: a section or a row
    shape the plugin does not know is a ValueError, never a silently dropped item. The console renders no team
    decisions (FYI) into the inbox, so that group stays empty."""
    groups: Dict[str, list] = {key: [] for key in ORDER + ("fyi",)}
    if dto.get("setup") is True:
        groups["setup"].append({"title": SETUP_TITLE, "url": _text(dto, "setupUrl")})
    if dto.get("paused") is True:
        groups["paused"].append({"title": "Run log", "url": _text(dto, "pausedUrl")})
    for row in dto.get("items") or []:
        number, section = row.get("number"), row.get("section")
        if section not in CONSOLE_SECTIONS or not isinstance(number, int) or isinstance(number, bool) \
                or number < 1 or not _text(row, "title"):
            raise ValueError(f"the console inbox has an item this plugin cannot read (section {section!r})")
        groups[section].append({"number": number, "key": _text(row, "key"), "title": row["title"],
                                "url": item_url(number) or _text(row, "url"), "ask": _text(row, "ask")})
    return groups


def flatten_newlines(text: str) -> str:
    """Collapse CRLF/CR/LF to a single space (team-console#661): shared by `escape_markdown` and the digest's
    plain-text render, since a raw newline would forge an extra line — e.g. a fake "Answer:" row — in either."""
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def escape_markdown(text: str) -> str:
    """Neutralize `title`/`ask` from the console (team-console#661): a raw title like `x](javascript:alert(1))`
    closes the markdown link early and opens its own. Escapes `\\ [ ] ( ) < > * _ \\`` and flattens newlines so
    the text can never span or spoof a link/line of its own."""
    escaped = _MARKDOWN_CONTROL.sub(lambda m: "\\" + m.group(0), text)
    return flatten_newlines(escaped)


def linkable(url: Any) -> Optional[str]:
    """`url` when it may go into a markdown link: https, or http to this machine (a local console). Anything else —
    `javascript:`, `data:`, a relative path, a space, parenthesis or angle bracket that would end the link — is None
    and the title is shown as plain text (team-console#628: the console's rows are data)."""
    if not isinstance(url, str) or any(c.isspace() or c in "()<>" for c in url):
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if not host or parts.username or parts.password:
        return None
    return url if parts.scheme == "https" or (parts.scheme == "http" and is_loopback(host)) else None


def item_line(item: dict) -> str:
    # The only place `escape_markdown` runs: `console_groups`/`sections` pass titles/asks through untouched, so
    # any other render of `item['title']`/`item['ask']` (the digest's plain text included) must flatten newlines
    # itself and never treat the text as safe markdown.
    number = f"#{item['number']} " if "number" in item else ""
    ask = f" — {escape_markdown(item['ask'])}" if item.get("ask") else ""
    url = linkable(item.get("url"))
    title = escape_markdown(item["title"])
    title = f"[{title}]({url})" if url else title
    return f"- {number}{title}{ask}"


def titled(groups: Dict[str, list], language: str = "en") -> List[Tuple[str, List[dict]]]:
    """The groups as the digest reads them: (section title, items with number as text or None, title, url, ask)."""
    t = TEXT.get(language, TEXT["en"])
    return [(t[key][0], [{"number": str(i["number"]) if "number" in i else None, "title": i["title"],
                          "url": i.get("url"), "ask": i.get("ask")} for i in groups[key]])
            for key in ORDER + ("fyi",) if groups[key]]


def render(issues: List[dict], paused_url: str = "", updated: str = "", same_account_url: str = "",
           decided: List[dict] = (), language: str = "en") -> str:
    """same_account_url: link to the owner checklist, set while the agents act as the owner's account."""
    return render_groups(sections(issues, paused_url, same_account_url, decided), updated, language)


def render_groups(groups: Dict[str, list], updated: str = "", language: str = "en",
                  actions: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    """`actions` replaces a section's action line per language (CONSOLE_ACTIONS for the console's inbox)."""
    t = TEXT.get(language, TEXT["en"])
    override = (actions or {}).get(language) or (actions or {}).get("en") or {}
    total = sum(len(groups[k]) for k in ORDER)
    lines = [MARKER, f"**{t['count'][0 if total == 1 else 1].format(n=total)}** {t['updated'].format(when=updated)}", ""]
    if not total:
        lines.append(t["none"])
    for key in ORDER + ("fyi",):
        items = groups[key]
        if not items:
            continue
        title, action = t[key]
        lines += [f"### {title} ({len(items)})", f"_{override.get(key, action)}_", ""]
        lines += [item_line(item) for item in items]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
