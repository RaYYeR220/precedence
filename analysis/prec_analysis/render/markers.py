"""Replace generated blocks between marker comments, leaving every other byte alone.

A block in a Markdown file looks like

    <!-- results:verdicts:start -->
    ...generated, replaced on every render...
    <!-- results:verdicts:end -->

Only the text strictly between the two comments changes. The file's own
line endings are kept, so a CRLF checkout renders to CRLF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MARKER = re.compile(r"<!-- results:(?P<name>[a-z0-9_]+):start -->(?P<body>.*?)<!-- results:(?P=name):end -->", re.DOTALL)
START = re.compile(r"<!-- results:([a-z0-9_]+):start -->")


@dataclass
class Replaced:
    text: str
    names: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    unclosed: list[str] = field(default_factory=list)


def replace_blocks(text: str, blocks: dict[str, str]) -> Replaced:
    """`text` with every known marker block's body replaced by `blocks[name]`."""
    newline = "\r\n" if "\r\n" in text else "\n"
    out = Replaced(text=text)

    def sub(m: re.Match[str]) -> str:
        name = m.group("name")
        out.names.append(name)
        if name not in blocks:
            out.unknown.append(name)
            return m.group(0)
        body = blocks[name].strip("\n").replace("\r\n", "\n").replace("\n", newline)
        new = f"{newline}{body}{newline}"
        if new != m.group("body"):
            out.changed.append(name)
        return f"<!-- results:{name}:start -->{new}<!-- results:{name}:end -->"

    out.text = MARKER.sub(sub, text)
    closed = set(out.names)
    out.unclosed = [n for n in START.findall(text) if n not in closed]
    return out


def update_file(path: Path, blocks: dict[str, str], check: bool = False) -> tuple[Replaced | None, list[str]]:
    """Render `blocks` into `path` (or, with `check`, only compare).

    Returns the replacement result (None when the file is missing) and
    warnings. Nothing is created: a missing file or a file without markers
    is a warning, never an error.
    """
    warnings: list[str] = []
    if not path.exists():
        return None, [f"{path}: not found, nothing rendered"]
    raw = path.read_bytes().decode("utf-8")
    res = replace_blocks(raw, blocks)
    if not res.names:
        warnings.append(f"{path}: no results markers found")
    for name in res.unknown:
        warnings.append(f"{path}: unknown block '{name}' left as is")
    for name in res.unclosed:
        warnings.append(f"{path}: block '{name}' has no end marker")
    if not check and res.text != raw:
        path.write_bytes(res.text.encode("utf-8"))
    return res, warnings
