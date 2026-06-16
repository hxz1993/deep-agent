"""Builtin MCP server: an agentic document assistant over the doc-center repo.

This is a standalone stdio MCP server. It does NOT import anything from the
``app`` package, so it can run as its own subprocess (the agent launches it via
``MCPServerStdio``). Keep builtin servers self-contained like this.

It exposes a small toolset that lets the agent navigate a documentation tree
(Sphinx-style ``rst`` for structure + ``md`` for content), search it by keyword,
read individual documents, and enumerate the screenshots a document references.

The doc root is read from the ``DOC_CENTER_ROOT`` environment variable, falling
back to ``DEFAULT_ROOT`` below.

Run it directly for a quick sanity check:
    python mcps/doc_search/server.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Fallback used when DOC_CENTER_ROOT is not set in the environment.
DEFAULT_ROOT = r"C:\Users\wei\MyGithub\doc-center-we\.repo\doc-center"

# Keep tool outputs bounded so they never blow up the model context window.
MAX_SEARCH_RESULTS = 8
MAX_MATCHES_PER_FILE = 3
MAX_READ_CHARS = 6000
SNIPPET_RADIUS = 1  # lines of context shown on each side of a match

# Directories excluded from the catalog (build artifacts / non-doc noise).
_SKIP_DIRS = {"logs"}

_IMG_RE = re.compile(r"!\[(?P<alt>.*?)\]\((?P<src>[^)]+)\)")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_RST_NAME_RE = re.compile(r"^\.\.\s+component:name::\s*(.+?)\s*$")
_RST_DESC_RE = re.compile(r"^\.\.\s+component:description::\s*(.+?)\s*$")
_RST_UNDERLINE_RE = re.compile(r"^[=~\-^\"'#*+.]{3,}\s*$")

INSTRUCTIONS = """\
You are a documentation assistant answering questions strictly from the
doc-center knowledge base. Use the doc_search_* tools and follow this workflow:

1. Start with `doc_catalog` to understand which component/section is relevant
   (you can drill in with the `subpath` argument).
2. Use `doc_search` with concise keywords to find candidate passages.
3. Use `doc_read` to read the most relevant document(s); use `section` to focus.
4. For UI/operation questions, call `doc_images` and list the relevant
   screenshot paths under a "相关截图" section so the user can open them.

Rules:
- Always cite the source document path(s) you relied on.
- List screenshot paths as references; do NOT describe or invent image contents
  (you cannot see the images).
- If the docs do not cover the question, say so plainly instead of guessing.
- Answer in the user's language.
"""

mcp = FastMCP("doc-search", instructions=INSTRUCTIONS)


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #
def _root() -> Path:
    return Path(os.environ.get("DOC_CENTER_ROOT", DEFAULT_ROOT)).resolve()


def _resolve(path: str) -> Path:
    """Resolve a user-supplied path (relative to root or absolute) safely.

    Raises ValueError if the resolved path escapes the doc root.
    """
    root = _root()
    candidate = Path(path)
    full = candidate if candidate.is_absolute() else root / candidate
    full = full.resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path {path!r} is outside the documentation root.") from exc
    return full


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(_root()).as_posix()
    except ValueError:
        return p.as_posix()


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Title / heading extraction
# --------------------------------------------------------------------------- #
def _md_title(p: Path) -> str:
    """Return the first H1 of a markdown file, else its stem."""
    try:
        for line in _read_text(p).splitlines():
            m = _MD_HEADING_RE.match(line)
            if m and len(m.group(1)) == 1:
                return m.group(2).strip()
    except OSError:
        pass
    return p.stem


def _rst_meta(p: Path) -> tuple[str, str]:
    """Return ``(title, description)`` parsed from an rst index file."""
    title = ""
    description = ""
    try:
        lines = _read_text(p).splitlines()
    except OSError:
        return (p.parent.name, "")

    prev = ""
    for line in lines:
        if not title:
            mn = _RST_NAME_RE.match(line)
            if mn:
                title = mn.group(1).strip()
        if not description:
            md = _RST_DESC_RE.match(line)
            if md:
                description = md.group(1).strip()
        # Fallback title: a text line directly followed by an rst underline.
        if not title and _RST_UNDERLINE_RE.match(line) and prev.strip() and not prev.startswith(".."):
            title = prev.strip()
        prev = line
    return (title or p.parent.name, description)


def _dir_title(d: Path) -> tuple[str, str]:
    """Best-effort ``(title, description)`` for a directory from its rst index."""
    for name in ("index.rst", "__index__.rst"):
        idx = d / name
        if idx.is_file():
            return _rst_meta(idx)
    return (d.name, "")


def _preceding_heading(lines: list[str], idx: int) -> str:
    """Return the nearest markdown heading at or before line index ``idx``."""
    for i in range(min(idx, len(lines) - 1), -1, -1):
        m = _MD_HEADING_RE.match(lines[i])
        if m:
            return m.group(2).strip()
    return ""


# --------------------------------------------------------------------------- #
# Catalog (cached for the process lifetime)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _catalog_lines() -> list[str]:
    """Build a compact indented tree of the whole doc set.

    One line per node: ``<indent><title>  [<relpath>]``. Directories use their
    rst index title (+ description); markdown files use their H1.
    """
    root = _root()
    out: list[str] = []
    if not root.is_dir():
        return [f"(documentation root not found: {root})"]

    def walk(d: Path, depth: int) -> None:
        entries = sorted(d.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        dirs = [e for e in entries if e.is_dir() and not e.name.startswith(".") and e.name not in _SKIP_DIRS]
        mds = [e for e in entries if e.is_file() and e.suffix.lower() == ".md"]
        indent = "  " * depth
        for sub in dirs:
            title, desc = _dir_title(sub)
            suffix = f" - {desc}" if desc else ""
            out.append(f"{indent}- [DIR] {title}{suffix}  [{_rel(sub)}]")
            walk(sub, depth + 1)
        for md in mds:
            out.append(f"{indent}- {_md_title(md)}  [{_rel(md)}]")

    walk(root, 0)
    return out


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def doc_catalog(subpath: str = "", max_depth: int = 3) -> str:
    """Return the documentation tree as an indented outline.

    Each line shows a section/document title and its path in ``[brackets]``;
    directories are marked ``[DIR]``. Use the bracketed path with ``doc_read``.

    Args:
        subpath: Limit the outline to this directory (relative to the doc root).
            Empty means the whole tree.
        max_depth: How many levels to show below ``subpath`` (>=1).
    """
    lines = _catalog_lines()
    if lines and lines[0].startswith("(documentation root not found"):
        return lines[0]

    base_rel = ""
    base_depth = 0
    if subpath:
        target = _resolve(subpath)
        if not target.is_dir():
            return f"Not a directory under the doc root: {subpath!r}"
        base_rel = _rel(target)
        base_depth = base_rel.count("/") + 1  # depth of children below subpath

    max_depth = max(1, max_depth)
    selected: list[str] = []
    for line in lines:
        path_in = line[line.rfind("[") + 1 : line.rfind("]")]
        if base_rel and not (path_in == base_rel or path_in.startswith(base_rel + "/")):
            continue
        depth = path_in.count("/")  # absolute depth; equals the line's indent level
        if depth - base_depth >= max_depth:
            continue
        selected.append(line)

    if not selected:
        return f"No entries found under {subpath!r}." if subpath else "Documentation tree is empty."
    return "\n".join(selected)


def _search_with_rg(query: str, regex: bool) -> list[tuple[str, int, str]]:
    rg = shutil.which("rg")
    if not rg:
        return []
    args = [rg, "--line-number", "--no-heading", "--ignore-case", "--color", "never", "--glob", "*.md"]
    if not regex:
        args.append("--fixed-strings")
    args += ["--", query, str(_root())]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    hits: list[tuple[str, int, str]] = []
    for line in proc.stdout.splitlines():
        # Format: <path>:<lineno>:<text>
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path, lineno, text = parts
        try:
            hits.append((path, int(lineno), text))
        except ValueError:
            continue
    return hits


def _search_with_python(query: str, regex: bool) -> list[tuple[str, int, str]]:
    try:
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
    hits: list[tuple[str, int, str]] = []
    for md in _root().rglob("*.md"):
        try:
            for i, line in enumerate(_read_text(md).splitlines(), start=1):
                if pattern.search(line):
                    hits.append((str(md), i, line))
        except OSError:
            continue
    return hits


@mcp.tool()
def doc_search(query: str, max_results: int = MAX_SEARCH_RESULTS, regex: bool = False) -> str:
    """Search the markdown docs for ``query`` and return matching snippets.

    Results are grouped by document and include the line number, the nearest
    preceding heading, and a small snippet of context. Prefer short keyword
    queries. Set ``regex=True`` to treat ``query`` as a regular expression.
    """
    if not query.strip():
        return "Empty query."

    hits = _search_with_rg(query, regex) or _search_with_python(query, regex)
    if not hits:
        return f"No matches for {query!r}."

    # Group hits by file, preserving order of first appearance.
    by_file: dict[str, list[tuple[int, str]]] = {}
    for path, lineno, text in hits:
        by_file.setdefault(path, []).append((lineno, text))

    blocks: list[str] = []
    for path, matches in list(by_file.items())[:max_results]:
        p = Path(path)
        try:
            lines = _read_text(p).splitlines()
        except OSError:
            lines = []
        title = _md_title(p)
        blocks.append(f"### {title}  [{_rel(p)}]  ({len(matches)} match(es))")
        for lineno, text in matches[:MAX_MATCHES_PER_FILE]:
            heading = _preceding_heading(lines, lineno - 1) if lines else ""
            start = max(0, lineno - 1 - SNIPPET_RADIUS)
            end = min(len(lines), lineno + SNIPPET_RADIUS) if lines else lineno
            ctx = lines[start:end] if lines else [text]
            ctx_start = start + 1 if lines else lineno
            snippet = "\n".join(f"  {ctx_start + j}| {c}" for j, c in enumerate(ctx))
            head = f" (under: {heading})" if heading else ""
            blocks.append(f"- line {lineno}{head}\n{snippet}")
        blocks.append("")

    note = ""
    if len(by_file) > max_results:
        note = f"\n(Showing {max_results} of {len(by_file)} matching documents; refine the query for more.)"
    return "\n".join(blocks).rstrip() + note


def _slice_section(text: str, section: str) -> str | None:
    """Return the markdown block under the heading matching ``section``."""
    lines = text.splitlines()
    target = section.strip().lower()
    start = None
    start_level = 0
    for i, line in enumerate(lines):
        m = _MD_HEADING_RE.match(line)
        if m and m.group(2).strip().lower() == target:
            start = i
            start_level = len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _MD_HEADING_RE.match(lines[j])
        if m and len(m.group(1)) <= start_level:
            end = j
            break
    return "\n".join(lines[start:end])


def _resolve_images_inline(text: str, base: Path) -> str:
    """Rewrite ``![alt](rel.png)`` so the target becomes an absolute path."""

    def repl(m: re.Match[str]) -> str:
        src = m.group("src").strip()
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        abs_path = (base.parent / src).resolve()
        return f"![{m.group('alt')}]({abs_path})"

    return _IMG_RE.sub(repl, text)


@mcp.tool()
def doc_read(path: str, section: str = "", max_chars: int = MAX_READ_CHARS) -> str:
    """Read a markdown document (or an rst index) from the doc root.

    Image links are rewritten to absolute file paths so you can cite them.
    Use ``section`` (an exact heading text) to return only that section.
    Output is truncated to ``max_chars`` characters.
    """
    p = _resolve(path)
    if not p.is_file():
        return f"File not found: {path!r}"
    try:
        text = _read_text(p)
    except OSError as exc:
        return f"Could not read {path!r}: {exc}"

    if section:
        sliced = _slice_section(text, section)
        if sliced is None:
            return f"Section {section!r} not found in {path!r}."
        text = sliced

    text = _resolve_images_inline(text, p)
    header = f"# Source: {_rel(p)}\n\n"
    body = text if len(text) <= max_chars else text[:max_chars] + "\n\n…(truncated; call doc_read with a section or larger max_chars)…"
    return header + body


@mcp.tool()
def doc_images(path: str) -> str:
    """List every image referenced by a document, with resolved paths.

    Returns one entry per image: its alt text, the path as written, the resolved
    absolute path, and the nearest preceding heading for context. Image contents
    are NOT inspected; only the references are reported.
    """
    p = _resolve(path)
    if not p.is_file():
        return f"File not found: {path!r}"
    try:
        lines = _read_text(p).splitlines()
    except OSError as exc:
        return f"Could not read {path!r}: {exc}"

    entries: list[str] = []
    for i, line in enumerate(lines):
        for m in _IMG_RE.finditer(line):
            src = m.group("src").strip()
            if src.startswith(("http://", "https://", "data:")):
                abs_path = src
            else:
                abs_path = str((p.parent / src).resolve())
            heading = _preceding_heading(lines, i)
            alt = m.group("alt").strip() or "(no alt)"
            head = f" | under: {heading}" if heading else ""
            entries.append(f"- alt: {alt} | src: {src} | abs: {abs_path}{head}")

    if not entries:
        return f"No images referenced in {_rel(p)}."
    return f"Images referenced by {_rel(p)} ({len(entries)}):\n" + "\n".join(entries)


if __name__ == "__main__":
    mcp.run()
