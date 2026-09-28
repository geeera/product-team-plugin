"""The daily push to the owner's phone: the pinned inbox, as a short plain-text message.

GitHub does not notify you about your own account's comments — and the agents write as the owner's account — so
the team's questions would never reach the phone through GitHub. The digest goes out through a separate channel.
"""
from __future__ import annotations

import re
from typing import List, Tuple

_HEADING = re.compile(r"^### (.+?) \((\d+)\)\s*$")
_ITEM = re.compile(r"^- (?:#(\d+) )?\[(.+?)\]\((\S+?)\)(?: — (.+))?$")

HEADER = {"en": "{product}: {n} need you", "ru": "{product}: нужно твоё внимание — {n}"}
ANSWER = {"en": "Answer", "ru": "Ответ"}
FYI_KEYS = ("Decided by the team", "Решено командой")


def parse_inbox(body: str) -> List[Tuple[str, List[dict]]]:
    sections: List[Tuple[str, List[dict]]] = []
    for line in (body or "").splitlines():
        heading = _HEADING.match(line)
        if heading:
            sections.append((heading.group(1), []))
            continue
        item = _ITEM.match(line)
        if item and sections:
            sections[-1][1].append({"number": item.group(1), "title": item.group(2), "url": item.group(3),
                                    "ask": item.group(4)})
    return sections


def is_fyi(title: str) -> bool:
    return title.startswith(FYI_KEYS)


def format_digest(product: str, sections: List[Tuple[str, List[dict]]], language: str = "en") -> str:
    needs = sum(len(items) for title, items in sections if not is_fyi(title))
    lines = [HEADER.get(language, HEADER["en"]).format(product=product, n=needs), ""]
    for title, items in sections:
        if not items:
            continue
        lines.append(title)
        for item in items:
            number = f"#{item['number']} " if item["number"] else ""
            lines.append(f"• {number}{item['title']}")
            if item["ask"]:
                lines.append(f"  {ANSWER.get(language, ANSWER['en'])}: {item['ask']}")
            lines.append(f"  {item['url']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def needs_count(sections: List[Tuple[str, List[dict]]]) -> int:
    return sum(len(items) for title, items in sections if not is_fyi(title))
