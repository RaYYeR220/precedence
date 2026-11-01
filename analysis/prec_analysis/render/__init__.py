"""Presentation layer: results → README/REPORT blocks and the static explorer.

    python -m prec_analysis render --data data --results results \\
        --readme README.md --report REPORT.md --site site/dist [--check]

Every number shown comes from `results/*.json` (and, for run bookkeeping,
from the raw files under `data/`). With `--check` nothing is written; the
command fails if any generated block or site file differs from what is on
disk, which proves the published numbers reproduce from the data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .blocks import BLOCKS, render_blocks
from .context import Context, load_context
from .markers import replace_blocks, update_file
from .predictions import RULES, evaluate
from .site import check_site, site_files, write_site

__all__ = ["BLOCKS", "RULES", "Context", "evaluate", "load_context", "render_blocks", "replace_blocks", "run_render", "add_parser"]


def run_render(
    data: Path | None,
    results: Path,
    docs: list[Path],
    site: Path | None,
    check: bool = False,
    templates: Path | None = None,
    out=sys.stdout,
    err=sys.stderr,
) -> int:
    """Render (or check) every target. Returns the process exit code."""
    if not results.is_dir():
        print(f"warning: results directory {results} not found; blocks will say so", file=err)
    if data is not None and not data.is_dir():
        print(f"warning: data directory {data} not found; run bookkeeping is left out", file=err)
        data = None
    ctx = load_context(data, results)
    blocks = render_blocks(ctx)
    stale: list[str] = []
    for path in docs:
        res, warnings = update_file(path, blocks, check=check)
        for w in warnings:
            print(f"warning: {w}", file=err)
        if res is None:
            continue
        if check:
            stale.extend(f"{path}:{name}" for name in res.changed)
        elif res.changed:
            print(f"{path}: updated {', '.join(res.changed)}", file=out)
        else:
            print(f"{path}: up to date ({len(res.names)} block(s))", file=out)
    if site is not None:
        files = site_files(ctx, templates)
        if check:
            stale.extend(f"{site / rel}" for rel in check_site(files, site))
        else:
            write_site(files, site)
            n = len(ctx.data.dataset.runs) if ctx.data else 0
            print(f"{site}: wrote {len(files)} files ({n} run(s))", file=out)
    if check:
        if stale:
            print("generated content differs from the files: " + ", ".join(stale), file=err)
            return 1
        print("every generated block and site file reproduces exactly", file=out)
    return 0


def _cmd(args: argparse.Namespace) -> int:
    docs = [p for p in (args.readme, args.report) if p is not None]
    return run_render(args.data, args.results, docs, args.site, check=args.check, templates=args.templates)


def add_parser(sub: argparse._SubParsersAction) -> None:
    r = sub.add_parser("render", help="results → README/REPORT blocks and the static explorer")
    r.add_argument("--data", type=Path, default=Path("data"))
    r.add_argument("--results", type=Path, default=Path("results"))
    r.add_argument("--readme", type=Path, default=None, help="Markdown file with results markers (e.g. README.md)")
    r.add_argument("--report", type=Path, default=None, help="a second Markdown file with results markers (e.g. REPORT.md)")
    r.add_argument("--site", type=Path, default=None, help="output directory of the explorer (e.g. site/dist)")
    r.add_argument("--templates", type=Path, default=None, help="template directory (default: analysis/templates)")
    r.add_argument("--check", action="store_true", help="write nothing; fail if any generated content differs")
    r.set_defaults(func=_cmd)
