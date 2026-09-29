"""Render model text into the restricted WeCom application Markdown dialect.

This is deliberately channel-specific: other transports keep their own renderers.
No HTML is generated, and content is never truncated to fit a message.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

MESSAGE_BYTES = 2048


def split_text(text: str, limit: int = MESSAGE_BYTES) -> list[str]:
    """Split UTF-8 text losslessly, preferring paragraph and line boundaries."""
    if limit < 4:
        raise ValueError("limit must accommodate a UTF-8 character")
    result: list[str] = []
    while text:
        if len(text.encode("utf-8")) <= limit:
            result.append(text)
            break
        prefix = text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
        boundary = prefix.rfind("\n\n")
        if boundary >= 0:
            size = boundary + 2
        else:
            boundary = prefix.rfind("\n")
            size = boundary + 1 if boundary >= 0 else len(prefix)
        result.append(text[:size])
        text = text[size:]
    return result


@dataclass(frozen=True)
class _Atom:
    value: str
    prefix: str = ""
    suffix: str = ""
    atomic: bool = False


def _inline(text: str, prefix: str = "", suffix: str = "") -> list[_Atom]:
    """Keep complete links atomic; styles can be reopened across messages."""
    atoms: list[_Atom] = []
    plain: list[str] = []

    def flush() -> None:
        if plain:
            atoms.append(_Atom("".join(plain), prefix, suffix))
            plain.clear()

    pos = 0
    while pos < len(text):
        # Links may contain balanced parentheses in their URL.
        start = pos + 1 if text.startswith("![", pos) else pos
        if text.startswith("[", start):
            label_end = text.find("](", start + 1)
            if label_end >= 0:
                end, depth = label_end + 2, 1
                while end < len(text) and depth:
                    if text[end] == "(":
                        depth += 1
                    elif text[end] == ")":
                        depth -= 1
                    end += 1
                if depth == 0:
                    flush()
                    label = text[start + 1:label_end] or "图片"
                    url = text[label_end + 2:end - 1]
                    link = f"[{label}]({url})"
                    if len((prefix + link + suffix).encode()) <= MESSAGE_BYTES:
                        atoms.append(_Atom(link, prefix, suffix, True))
                    else:
                        # An oversized URL cannot remain a clickable atomic link.
                        atoms.append(_Atom(f"{label} ({url})", prefix, suffix))
                    pos = end
                    continue
        marker = "**" if text.startswith("**", pos) else ""
        if text[pos] == "`":
            marker = re.match(r"`+", text[pos:])[0]
        if marker:
            end = text.find(marker, pos + len(marker))
            if end > pos + len(marker):
                flush()
                value = text[pos + len(marker):end]
                if marker == "**":
                    atoms.extend(_inline(value, prefix + marker, marker + suffix))
                else:
                    if "`" in value:
                        atoms.append(_Atom(value, prefix, suffix))
                    else:
                        atoms.append(_Atom(value, prefix + "`", "`" + suffix))
                pos = end + len(marker)
                continue
        plain.append(text[pos])
        pos += 1
    flush()
    return atoms


def _cells(line: str) -> list[str]:
    return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _blocks(text: str) -> list[_Atom]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True)
    result: list[_Atom] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            result.extend(_inline("".join(pending)))
            pending.clear()

    index = 0
    fence: str | None = None
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        valid_open = match and fence is None and (
            match[1][0] != "`" or "`" not in line[match.end():]
        )
        valid_close = match and fence is not None and (
            match[1][0] == fence[0] and len(match[1]) >= len(fence)
            and not line[match.end():].strip()
        )
        if valid_open or valid_close:
            flush()
            fence = match[1] if fence is None else None
            index += 1
            continue
        if fence:
            # Keep source characters/indentation; do not interpret code as Markdown.
            result.append(_Atom(line))
            index += 1
            continue
        if "|" in line and index + 1 < len(lines):
            separators = _cells(lines[index + 1])
            if separators and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separators):
                flush()
                headers = _cells(line)
                index += 2
                row_start = index
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    values = _cells(lines[index])
                    fields = [
                        f"{headers[n] if n < len(headers) else f'字段{n + 1}'}："
                        f"{values[n] if n < len(values) else ''}"
                        for n in range(max(len(headers), len(values)))
                    ]
                    result.extend(_inline("；".join(fields) + "\n"))
                    index += 1
                if index == row_start:
                    result.extend(_inline("；".join(headers) + "\n"))
                continue
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", line)
        if heading:
            flush()
            result.extend(_inline(f"**{heading[1]}**" + ("\n" if line.endswith("\n") else "")))
        else:
            pending.append(line)
        index += 1
    flush()
    return result


def format_markdown(text: str) -> list[str]:
    """Return independently formatted Markdown messages of at most 2048 bytes."""
    messages: list[str] = []
    current = ""
    for atom in _blocks(text):
        rendered = atom.prefix + atom.value + atom.suffix
        if len((current + rendered).encode()) <= MESSAGE_BYTES:
            current += rendered
            continue
        if current.endswith("\n"):
            messages.append(current)
            current = ""
        if atom.atomic:
            if current:
                messages.append(current)
            current = rendered
            continue
        remaining = atom.value
        overhead = len((atom.prefix + atom.suffix).encode())
        while remaining:
            budget = MESSAGE_BYTES - len(current.encode()) - overhead
            if budget < 4:
                if current:
                    messages.append(current)
                    current = ""
                budget = MESSAGE_BYTES - overhead
            pieces = split_text(remaining, budget)
            piece = pieces[0]
            current += atom.prefix + piece + atom.suffix
            remaining = remaining[len(piece):]
            if remaining:
                messages.append(current)
                current = ""
    if current:
        messages.append(current)
    return [message for message in messages if message.strip()]
