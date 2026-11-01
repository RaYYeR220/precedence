"""Read the raw `data/` tree (docs/DATA.md) into plain Python structures.

Only `design.json` and `probes.jsonl` are required for a run. Every other file
is optional: a missing or unreadable file disables the features that need it
and is recorded in `issues`, it never stops the analysis.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

NODES_FILE = re.compile(r"^nodes-(\d+)\.json(\.gz)?$")


@dataclass
class Snapshot:
    """One `getClusterNodes` response and when it was taken."""

    taken_unix_us: int
    nodes: dict[str, dict[str, Any]]

    def client_id(self, pubkey: str | None) -> str | None:
        if pubkey is None:
            return None
        node = self.nodes.get(pubkey)
        if node is None:
            return None
        return client_id_of(node)


@dataclass
class Run:
    """Everything one run directory holds."""

    run_id: str
    path: Path | None
    config: dict[str, Any]
    design: dict[str, Any]
    windows: list[dict[str, Any]]
    probes: list[dict[str, Any]]
    statuses: dict[str, dict[str, Any] | None]
    slots: list[dict[str, Any]]
    entries: list[dict[str, Any]] | None
    footers: list[dict[str, Any]] | None
    snapshots: list[Snapshot]
    issues: list[str] = field(default_factory=list)

    @property
    def batches(self) -> list[dict[str, Any]]:
        return list(self.design.get("batches", []))


@dataclass
class Dataset:
    """All runs of a data directory plus the blocks their probes landed in."""

    runs: list[Run]
    blocks: dict[int, dict[str, Any]]
    issues: list[str] = field(default_factory=list)


def client_id_of(node: dict[str, Any]) -> str:
    """Family label of a gossip node: its `clientId`, as the harness parses it."""
    cid = node.get("clientId", node.get("client_id"))
    if cid is None:
        return "Unknown"
    if isinstance(cid, (int, float)):
        return f"client-{int(cid)}"
    return str(cid)


def _to_unix_us(stamp: int) -> int:
    """File-name timestamps are µs in the harness; accept s and ms as well."""
    if stamp > 10**14:
        return stamp
    if stamp > 10**11:
        return stamp * 1_000
    return stamp * 1_000_000


def read_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as fh:
            return json.loads(fh.read())
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path, issues: list[str] | None = None) -> list[dict[str, Any]]:
    """Read a JSON-lines file, skipping blank and malformed lines (recorded in `issues`)."""
    out: list[dict[str, Any]] = []
    bad = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(value, dict):
                out.append(value)
            else:
                bad += 1
    if bad and issues is not None:
        issues.append(f"{path.name}: {bad} malformed line(s) skipped")
    return out


def _optional_jsonl(path: Path, issues: list[str]) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        return read_jsonl(path, issues)
    except OSError as exc:
        issues.append(f"{path.name}: unreadable ({exc})")
        return None


def load_snapshots(snapshot_dir: Path, issues: list[str]) -> list[Snapshot]:
    """Every `nodes-<unix>.json` in a run's snapshot directory, oldest first."""
    if not snapshot_dir.is_dir():
        return []
    snaps: list[Snapshot] = []
    for path in sorted(snapshot_dir.iterdir()):
        match = NODES_FILE.match(path.name)
        if not match:
            continue
        try:
            raw = read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"{path.name}: unreadable ({exc})")
            continue
        if isinstance(raw, dict):
            raw = raw.get("result", [])
        nodes = {n["pubkey"]: n for n in raw if isinstance(n, dict) and "pubkey" in n}
        snaps.append(Snapshot(taken_unix_us=_to_unix_us(int(match.group(1))), nodes=nodes))
    snaps.sort(key=lambda s: s.taken_unix_us)
    return snaps


def load_run(run_dir: Path) -> Run:
    """Load one run directory."""
    run_dir = Path(run_dir)
    issues: list[str] = []
    design_path = run_dir / "design.json"
    probes_path = run_dir / "probes.jsonl"
    if not design_path.exists():
        raise FileNotFoundError(f"{design_path} is required")
    design = read_json(design_path)
    probes = read_jsonl(probes_path, issues) if probes_path.exists() else []
    if not probes_path.exists():
        issues.append("probes.jsonl missing: no probe was sent")
    config_path = run_dir / "config.json"
    config = read_json(config_path) if config_path.exists() else {}
    statuses_path = run_dir / "statuses.json"
    statuses: dict[str, dict[str, Any] | None] = {}
    if statuses_path.exists():
        statuses = read_json(statuses_path)
    else:
        issues.append("statuses.json missing: every probe counts as not landed")
    windows = _optional_jsonl(run_dir / "windows.jsonl", issues) or []
    slots = _optional_jsonl(run_dir / "slots.jsonl", issues) or []
    entries = _optional_jsonl(run_dir / "entries.jsonl", issues)
    footers = _optional_jsonl(run_dir / "footers.jsonl", issues)
    snapshots = load_snapshots(run_dir / "snapshot", issues)
    if not snapshots:
        issues.append("no nodes-<unix>.json snapshot: families fall back to the planned label")
    run_id = str(design.get("run_id") or config.get("run_id") or run_dir.name)
    return Run(
        run_id=run_id,
        path=run_dir,
        config=config,
        design=design,
        windows=windows,
        probes=probes,
        statuses=statuses,
        slots=slots,
        entries=entries,
        footers=footers,
        snapshots=snapshots,
        issues=issues,
    )


def landed_slots(runs: Iterable[Run]) -> set[int]:
    """Every slot any probe of these runs landed in."""
    out: set[int] = set()
    for run in runs:
        for status in run.statuses.values():
            if status and status.get("slot") is not None:
                out.add(int(status["slot"]))
    return out


def load_blocks(blocks_dir: Path, slots: Iterable[int] | None = None, issues: list[str] | None = None) -> dict[int, dict[str, Any]]:
    """Load `<slot>.json.gz` (or `.json`) block files; only `slots` when given."""
    blocks: dict[int, dict[str, Any]] = {}
    if not blocks_dir.is_dir():
        return blocks
    if slots is None:
        candidates = sorted(p for p in blocks_dir.iterdir() if p.name.split(".")[0].isdigit())
    else:
        candidates = []
        for slot in sorted(set(slots)):
            for name in (f"{slot}.json.gz", f"{slot}.json"):
                path = blocks_dir / name
                if path.exists():
                    candidates.append(path)
                    break
    for path in candidates:
        try:
            block = read_json(path)
        except (OSError, json.JSONDecodeError, EOFError) as exc:
            if issues is not None:
                issues.append(f"blocks/{path.name}: unreadable ({exc})")
            continue
        slot = int(block.get("slot", path.name.split(".")[0]))
        blocks[slot] = block
    return blocks


def find_run_dirs(data_dir: Path) -> list[Path]:
    """Run directories under `data_dir/runs`, or `data_dir` itself if it is one."""
    data_dir = Path(data_dir)
    if (data_dir / "design.json").exists():
        return [data_dir]
    runs_dir = data_dir / "runs"
    if not runs_dir.is_dir():
        return []
    return sorted(p for p in runs_dir.iterdir() if (p / "design.json").exists())


def blocks_dir_for(data_dir: Path) -> Path:
    data_dir = Path(data_dir)
    if (data_dir / "design.json").exists():
        return data_dir.parent.parent / "blocks"
    return data_dir / "blocks"


def load_dataset(data_dir: Path | str) -> Dataset:
    """Load every run of a data directory and the blocks its probes landed in."""
    data_dir = Path(data_dir)
    runs = [load_run(p) for p in find_run_dirs(data_dir)]
    issues: list[str] = []
    for run in runs:
        issues.extend(f"{run.run_id}: {msg}" for msg in run.issues)
    wanted = landed_slots(runs)
    blocks = load_blocks(blocks_dir_for(data_dir), wanted, issues)
    missing = sorted(wanted - set(blocks))
    if missing:
        issues.append(f"{len(missing)} landed slot(s) without a block file")
    return Dataset(runs=runs, blocks=blocks, issues=issues)
