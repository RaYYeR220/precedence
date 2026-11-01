"""Everything the render step reads: the results JSON and a few facts from `data/`.

Results come from `results/*.json` exactly as `analyze` wrote them. The data
directory is read only for what the results do not carry: run bookkeeping
(times, skip reasons, send errors, the spend ledger), stake from the
snapshot, the advertised TPU address of each window, and the block contents
the explorer draws. Attribution of probes to blocks reuses `tidy`, so the
explorer and the analysis can never disagree on where a probe landed.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..load import Dataset, client_id_of, find_run_dirs, load_dataset, read_json, read_jsonl
from ..tidy import probes_frame

RESULT_FILES = (
    "summary",
    "pairs_by_condition",
    "models",
    "inclusion",
    "fingerprint",
    "baseline",
    "controls",
    "power",
    "entries",
    "simulation_validation",
)


@dataclass
class RunFacts:
    """Bookkeeping of one run, straight from its files."""

    run_id: str
    mode: str | None
    started_unix_us: int | None
    ended_unix_us: int | None
    n_batches: int
    status_counts: dict[str, int]
    first_slot: int | None
    last_slot: int | None
    n_probes: int
    n_sent: int
    n_landed: int
    send_errors: dict[str, int]
    reserved: int
    settled: int
    n_reserved: int
    n_settled: int


@dataclass
class DataFacts:
    dataset: Dataset
    probes: pd.DataFrame
    runs: list[RunFacts]
    ledger: list[dict[str, Any]]
    stake: dict[str, int]
    client_of: dict[str, str]
    proxy_by_family: dict[str, tuple[int, int]]
    issues: list[str] = field(default_factory=list)

    @property
    def total_stake(self) -> int:
        return int(sum(self.stake.values()))

    def stake_share_of_family(self, family: str) -> float | None:
        total = self.total_stake
        if not total or not self.client_of:
            return None
        return sum(s for k, s in self.stake.items() if self.client_of.get(k) == family) / total

    def stake_share_of(self, pubkey: str) -> float | None:
        total = self.total_stake
        if not total:
            return None
        return self.stake.get(pubkey, 0) / total


@dataclass
class Context:
    results: dict[str, Any]
    data: DataFacts | None

    def r(self, name: str) -> dict[str, Any]:
        value = self.results.get(name)
        return value if isinstance(value, dict) else {}

    @property
    def summary(self) -> dict[str, Any]:
        return self.r("summary")

    def family(self, name: str) -> dict[str, Any] | None:
        fam = self.summary.get("families", {}).get(name)
        return fam if isinstance(fam, dict) else None

    def validators(self) -> dict[str, dict[str, Any]]:
        return self.summary.get("validators", {}) or {}

    def families(self) -> dict[str, dict[str, Any]]:
        return self.summary.get("families", {}) or {}


def load_results(results_dir: Path | str | None) -> dict[str, Any]:
    """Every known results file that exists; missing or unreadable ones are left out."""
    out: dict[str, Any] = {}
    if results_dir is None:
        return out
    base = Path(results_dir)
    for name in RESULT_FILES:
        path = base / f"{name}.json"
        if not path.exists():
            continue
        try:
            out[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _stake_snapshot(run_dirs: list[Path]) -> tuple[dict[str, int], dict[str, str]]:
    """Activated stake per node identity and its clientId, from the earliest run's snapshot."""
    for run_dir in run_dirs:
        snap = run_dir / "snapshot"
        votes_path = snap / "vote_accounts.json"
        if not votes_path.exists():
            continue
        try:
            votes = read_json(votes_path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(votes, dict) and "result" in votes:
            votes = votes["result"]
        stake: Counter[str] = Counter()
        for key in ("current", "delinquent"):
            for acc in (votes or {}).get(key, []) or []:
                node, s = acc.get("nodePubkey"), acc.get("activatedStake")
                if isinstance(node, str) and isinstance(s, (int, float)):
                    stake[node] += int(s)
        client_of: dict[str, str] = {}
        nodes_files = sorted(snap.glob("nodes-*.json*"))
        if nodes_files:
            try:
                raw = read_json(nodes_files[0])
            except (OSError, json.JSONDecodeError):
                raw = []
            if isinstance(raw, dict):
                raw = raw.get("result", [])
            for node in raw or []:
                if isinstance(node, dict) and "pubkey" in node:
                    client_of[node["pubkey"]] = client_id_of(node)
        return dict(stake), client_of
    return {}, {}


def _ledger(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / "ledger" / "spend.jsonl"
    if not path.exists():
        return []
    try:
        return read_jsonl(path)
    except OSError:
        return []


def _run_facts(ds: Dataset, probes: pd.DataFrame, ledger: list[dict[str, Any]]) -> list[RunFacts]:
    out = []
    for run in ds.runs:
        windows = {int(w["batch_id"]): w for w in run.windows if "batch_id" in w}
        statuses: Counter[str] = Counter()
        slots = []
        for b in run.batches:
            w = windows.get(int(b["batch_id"]))
            statuses[str(w.get("status", "missing")) if w else "missing"] += 1
            if b.get("window_first_slot") is not None:
                slots.append(int(b["window_first_slot"]))
        mine = probes[probes["run_id"] == run.run_id] if not probes.empty else probes
        errors: Counter[str] = Counter(str(p["send_err"]) for p in run.probes if p.get("send_err"))
        spend = [e for e in ledger if e.get("run_id") == run.run_id]
        reserves = [e for e in spend if e.get("kind") == "reserve"]
        settles = [e for e in spend if e.get("kind") == "settle"]
        out.append(
            RunFacts(
                run_id=run.run_id,
                mode=run.config.get("mode"),
                started_unix_us=run.config.get("started_unix_us"),
                ended_unix_us=run.config.get("ended_unix_us"),
                n_batches=len(run.batches),
                status_counts=dict(sorted(statuses.items())),
                first_slot=min(slots) if slots else None,
                last_slot=(max(slots) + 3) if slots else None,
                n_probes=len(run.probes),
                n_sent=int(mine["sent"].sum()) if len(mine) else 0,
                n_landed=int(mine["landed"].sum()) if len(mine) else 0,
                send_errors=dict(sorted(errors.items())),
                reserved=int(sum(int(e.get("lamports", 0)) for e in reserves)),
                settled=int(sum(int(e.get("lamports", 0)) for e in settles)),
                n_reserved=len(reserves),
                n_settled=len(settles),
            )
        )
    return out


def _proxy_by_family(ds: Dataset) -> dict[str, tuple[int, int]]:
    """(windows whose tpuQuic is a proxy, windows) per planned family."""
    counts: dict[str, list[int]] = {}
    for run in ds.runs:
        windows = {int(w["batch_id"]): w for w in run.windows if "batch_id" in w}
        for b in run.batches:
            w = windows.get(int(b["batch_id"]), {})
            proxy = w.get("tpu_is_proxy", b.get("tpu_is_proxy"))
            if proxy is None:
                continue
            fam = str(b.get("family", w.get("family", "Unknown")))
            c = counts.setdefault(fam, [0, 0])
            c[0] += int(bool(proxy))
            c[1] += 1
    return {k: (v[0], v[1]) for k, v in sorted(counts.items())}


def load_data_facts(data_dir: Path | str | None) -> DataFacts | None:
    """Facts from the data tree, or None when there is no run in it."""
    if data_dir is None:
        return None
    base = Path(data_dir)
    run_dirs = find_run_dirs(base)
    if not run_dirs:
        return None
    ds = load_dataset(base)
    probes = probes_frame(ds.runs, ds.blocks)
    ledger = _ledger(base)
    stake, client_of = _stake_snapshot(run_dirs)
    return DataFacts(
        dataset=ds,
        probes=probes,
        runs=_run_facts(ds, probes, ledger),
        ledger=ledger,
        stake=stake,
        client_of=client_of,
        proxy_by_family=_proxy_by_family(ds),
        issues=list(ds.issues),
    )


def load_context(data_dir: Path | str | None, results_dir: Path | str | None) -> Context:
    return Context(results=load_results(results_dir), data=load_data_facts(data_dir))
