"""Everything the render step reads: the results JSON and a few facts from `data/`.

Results come from `results/*.json` exactly as `analyze` wrote them. The data
directory is read only for what the results do not carry: run bookkeeping
(times, skip reasons, send errors, the spend ledger), the leader population
in the snapshot (stake, versions, TPU addresses), the slot clock, and a few
splits the pre-registered predictions need (by send anchor, and who produced
a block a probe landed in early). Each of those is one small method of
`DataFacts` below. Attribution of probes to blocks reuses `tidy`, and pair
rates reuse `stats.pair_rate` with the analysis' own settings, so a number
computed here is computed exactly as the analysis would.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..constants import N_BOOT, N_PERM, SLOTS_PER_WINDOW
from ..inclusion import wilson
from ..load import Dataset, Run, Snapshot, client_id_of, find_run_dirs, load_dataset, read_json, read_jsonl
from ..models import resample_counts, seed_for
from ..orders import OrderSet
from ..stats import bb_mask, pair_rate, priority_pred
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


def anchor_label(anchor: dict[str, Any] | None) -> str:
    """'before the window, 3 slots', 'in the window, +1 slot', or 'unknown'."""
    if not anchor or anchor.get("type") is None:
        return "unknown"
    n = anchor.get("slots")
    unit = "slot" if n == 1 else "slots"
    n_txt = f"{float(n):g} {unit}" if isinstance(n, (int, float)) else "?"
    return f"before the window, {n_txt}" if anchor["type"] == "before_window" else f"in the window, +{n_txt}"


def _ip(addr: Any) -> str | None:
    """Host part of 'ip:port' or '[ipv6]:port'."""
    if not isinstance(addr, str) or ":" not in addr:
        return None
    return addr.rsplit(":", 1)[0].strip("[]")


def _quantiles(values: list[float], qs: tuple[float, ...] = (0.1, 0.5, 0.9)) -> list[float] | None:
    if not values:
        return None
    return [float(np.quantile(np.asarray(values, dtype=float), q)) for q in qs]


@dataclass
class DataFacts:
    dataset: Dataset
    probes: pd.DataFrame
    runs: list[RunFacts]
    ledger: list[dict[str, Any]]
    stake: dict[str, int]
    client_of: dict[str, str]
    reference_nodes: dict[str, dict[str, Any]]
    reference_time_us: int | None
    proxy_by_family: dict[str, tuple[int, int]]
    issues: list[str] = field(default_factory=list)
    n_boot: int = N_BOOT
    n_perm: int = N_PERM

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

    # -- splits the results do not carry -------------------------------------------

    @cached_property
    def order_set(self) -> OrderSet:
        return OrderSet.from_probes(self.probes) if not self.probes.empty else OrderSet.empty()

    def _ladder_mask(self, families: tuple[str, ...], anchor_type: str) -> np.ndarray:
        os = self.order_set
        fam = os.bbs["producer_family"].astype(str).to_numpy()
        return bb_mask(os, kind="ladder", anchor_type=anchor_type) & np.isin(fam, list(families))

    def ladder_priority_by_anchor(self, families: tuple[str, ...], anchor_type: str) -> dict[str, Any] | None:
        """Higher-priority-first rate of ladder pairs whose batch was anchored `anchor_type`
        (`before_window` or `in_window`, from design.json) in blocks produced by `families`.
        Same estimator, interval and permutation test as `pairs_by_condition`.
        """
        os = self.order_set
        if os.n_bb == 0:
            return None
        sub = os.subset(self._ladder_mask(families, anchor_type))
        if sub.n_bb == 0:
            return None
        return pair_rate(sub, priority_pred(sub), f"render|anchor|{'+'.join(families)}|{anchor_type}", self.n_boot, self.n_perm)

    def ladder_anchor_difference(self, families: tuple[str, ...]) -> dict[str, Any]:
        """Before-window minus in-window higher-priority-first rate on ladder pairs, with a
        95% interval from resampling batches independently in each group.
        """
        os = self.order_set
        out: dict[str, Any] = {"value": None, "ci": [None, None]}
        if os.n_bb == 0:
            return out
        boots, rates = [], []
        for anchor in ("before_window", "in_window"):
            sub = os.subset(self._ladder_mask(families, anchor))
            pred = priority_pred(sub)
            use = pred != 0
            if not use.any():
                return out
            hit = ((pred * sub.obs) == 1) & use
            clusters = sub.bbs["cluster"].to_numpy()[sub.pair_bb][use]
            codes, inv = np.unique(clusters, return_inverse=True)
            s_c = np.bincount(inv, weights=hit[use].astype(float), minlength=codes.size)
            n_c = np.bincount(inv, minlength=codes.size).astype(float)
            k = resample_counts(np.random.default_rng(seed_for("render|anchor-diff", "+".join(families), anchor)), codes.size, self.n_boot)
            with np.errstate(invalid="ignore", divide="ignore"):
                boots.append((k @ s_c) / (k @ n_c))
            rates.append(float(s_c.sum() / n_c.sum()))
        diff = boots[0] - boots[1]
        out["value"] = rates[0] - rates[1]
        out["ci"] = [float(np.nanquantile(diff, 0.025)), float(np.nanquantile(diff, 0.975))]
        return out

    def landing_by_anchor(self, family: str) -> dict[str, dict[str, Any]]:
        """Landing rate of sent probes whose targeted leader was `family` at window time,
        by send anchor type, with Wilson intervals. Keys: `before_window`, `in_window`.
        """
        p = self.probes
        if p.empty:
            return {}
        sent = p[p["sent"].astype(bool) & (p["target_family"].astype(str) == family)]
        out = {}
        for anchor, grp in sent.groupby("anchor_type", sort=True):
            k, n = int(grp["landed"].astype(bool).sum()), int(len(grp))
            out[str(anchor)] = {"k": k, "n": n, "rate": k / n if n else None, "ci": wilson(k, n)}
        return out

    def early_landings(self, families: tuple[str, ...]) -> dict[str, Any]:
        """Probes aimed at `families` that landed in a block before their target window: how
        many, and the client family of the leader that produced each such block (block
        `leader` matched to the gossip snapshot nearest the window).
        """
        p = self.probes
        if p.empty:
            return {"n_landed": 0, "n_early": 0, "producers": {}}
        aimed = p[p["in_block"].astype(bool) & p["target_family"].astype(str).isin(list(families))]
        offset = pd.to_numeric(aimed["slot_in_window"], errors="coerce")
        early = aimed[offset < 0]
        return {
            "n_landed": int(len(aimed)),
            "n_early": int(len(early)),
            "producers": dict(sorted(Counter(early["producer_family"].fillna("Unknown").astype(str)).items())),
        }

    # -- the leader population -------------------------------------------------------

    def leader_types(self) -> list[dict[str, Any]]:
        """Per client family, from the gossip snapshot nearest the first run's start: nodes,
        staked validators, stake share, share whose tpuQuic host differs from the gossip
        host, and the most common version among staked nodes (all nodes without stake data).
        """
        if not self.reference_nodes:
            return []
        total = self.total_stake
        by_fam: dict[str, list[dict[str, Any]]] = {}
        for pk, node in self.reference_nodes.items():
            by_fam.setdefault(client_id_of(node), []).append({**node, "pubkey": pk})
        rows = []
        for fam, nodes in by_fam.items():
            staked = [n for n in nodes if self.stake.get(n["pubkey"], 0) > 0]
            both = [n for n in nodes if _ip(n.get("tpuQuic")) and _ip(n.get("gossip"))]
            proxy = [n for n in both if _ip(n.get("tpuQuic")) != _ip(n.get("gossip"))]
            pool = staked if staked else nodes
            versions = Counter(str(n.get("version")) for n in pool if n.get("version"))
            top = versions.most_common(1)[0] if versions else (None, 0)
            rows.append(
                {
                    "family": fam,
                    "n_nodes": len(nodes),
                    "n_staked": len(staked) if total else None,
                    "stake_share": (sum(self.stake.get(n["pubkey"], 0) for n in nodes) / total) if total else None,
                    "share_tpu_differs": (len(proxy) / len(both)) if both else None,
                    "n_with_addresses": len(both),
                    "version": top[0],
                    "version_share": (top[1] / len(pool)) if pool and top[0] else None,
                }
            )
        rows.sort(key=lambda r: (-(r["stake_share"] or 0), -r["n_nodes"], r["family"]))
        return rows

    # -- the slot clock ---------------------------------------------------------------

    @cached_property
    def slot_times(self) -> dict[int, int]:
        """Exact first-shred time per slot: `server_unix_us` of the websocket events in
        slots.jsonl (the node's own timestamp), the earliest per slot.
        """
        out: dict[int, int] = {}
        for run in self.dataset.runs:
            for row in run.slots:
                t = row.get("server_unix_us")
                if row.get("source") != "ws" or t is None or row.get("slot") is None:
                    continue
                slot = int(row["slot"])
                out[slot] = min(int(t), out.get(slot, int(t)))
        return out

    def clock(self) -> dict[str, Any]:
        """Window timing from the exact slot times, and where our first sends fell.

        Leader windows are the 4-slot groups starting at slots divisible by 4.
        * offsets: median time of slot k of a window after its first slot, k = 1..3;
        * period: time from one window's first slot to the next one's (p10/p50/p90);
        * send phase: per sent batch, first send minus the exact time of its window's
          first slot, grouped by the batch's send anchor (p10/p50/p90), next to the
          planned offset (anchor slots × the clock fit's slot length at send time).
        """
        t = self.slot_times
        out: dict[str, Any] = {"n_slots": len(t), "offsets": [], "period": None, "send_phase": []}
        if not t:
            return out
        starts = sorted(s for s in t if s % SLOTS_PER_WINDOW == 0)
        for k in range(1, SLOTS_PER_WINDOW):
            d = [(t[s + k] - t[s]) / 1000.0 for s in starts if s + k in t]
            out["offsets"].append({"slot": k, "n": len(d), "median_ms": float(np.median(d)) if d else None})
        periods = [(t[s + SLOTS_PER_WINDOW] - t[s]) / 1000.0 for s in starts if s + SLOTS_PER_WINDOW in t]
        out["period"] = {"n": len(periods), "q_ms": _quantiles(periods)}
        groups: dict[str, dict[str, list[float]]] = {}
        for run in self.dataset.runs:
            windows = {int(w["batch_id"]): w for w in run.windows if "batch_id" in w}
            for b in run.batches:
                w = windows.get(int(b["batch_id"]), {})
                ws, first = b.get("window_first_slot", w.get("window_first_slot")), w.get("first_send_unix_us")
                if ws is None or first is None or int(ws) not in t:
                    continue
                anchor = (b.get("design") or {}).get("anchor") or {}
                g = groups.setdefault(anchor_label(anchor), {"phase": [], "planned": []})
                g["phase"].append((int(first) - t[int(ws)]) / 1000.0)
                if w.get("slot_us") is not None and isinstance(anchor.get("slots"), (int, float)):
                    sign = -1.0 if anchor.get("type") == "before_window" else 1.0
                    g["planned"].append(sign * float(anchor["slots"]) * float(w["slot_us"]) / 1000.0)
        for label in sorted(groups):
            g = groups[label]
            out["send_phase"].append(
                {"anchor": label, "n": len(g["phase"]), "q_ms": _quantiles(g["phase"]), "planned_ms": float(np.median(g["planned"])) if g["planned"] else None}
            )
        return out


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

    def card(self, family: str | None = None, validator: str | None = None) -> dict[str, Any] | None:
        """`summary.families[f].rule_card` or `summary.validators[v].rule_card`."""
        unit = self.family(family) if family is not None else self.validators().get(validator or "")
        card = (unit or {}).get("rule_card")
        return card if isinstance(card, dict) else None


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


def _read_stake(run_dir: Path | None) -> dict[str, int]:
    """Activated stake per node identity from a run's `snapshot/vote_accounts.json`."""
    if run_dir is None:
        return {}
    path = run_dir / "snapshot" / "vote_accounts.json"
    if not path.exists():
        return {}
    try:
        votes = read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(votes, dict) and "result" in votes:
        votes = votes["result"]
    stake: Counter[str] = Counter()
    for key in ("current", "delinquent"):
        for acc in (votes or {}).get(key, []) or []:
            node, s = acc.get("nodePubkey"), acc.get("activatedStake")
            if isinstance(node, str) and isinstance(s, (int, float)):
                stake[node] += int(s)
    return dict(stake)


def reference_snapshot(runs: list[Run]) -> tuple[Run | None, Snapshot | None]:
    """The first run (by start time) and its gossip snapshot nearest that start."""
    with_snaps = [r for r in runs if r.snapshots]
    if not with_snaps:
        return None, None
    run = min(with_snaps, key=lambda r: (r.config.get("started_unix_us") or r.snapshots[0].taken_unix_us, r.run_id))
    start = run.config.get("started_unix_us") or run.snapshots[0].taken_unix_us
    snap = min(run.snapshots, key=lambda s: (abs(s.taken_unix_us - int(start)), s.taken_unix_us))
    return run, snap


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
                last_slot=(max(slots) + SLOTS_PER_WINDOW - 1) if slots else None,
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
    """(windows whose tpuQuic is a proxy, windows) per planned family, from windows.jsonl."""
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


def load_data_facts(data_dir: Path | str | None, settings: dict[str, Any] | None = None) -> DataFacts | None:
    """Facts from the data tree, or None when there is no run in it."""
    if data_dir is None:
        return None
    base = Path(data_dir)
    if not find_run_dirs(base):
        return None
    ds = load_dataset(base)
    probes = probes_frame(ds.runs, ds.blocks)
    ledger = _ledger(base)
    ref_run, ref_snap = reference_snapshot(ds.runs)
    stake = _read_stake(ref_run.path if ref_run is not None else None)
    nodes = ref_snap.nodes if ref_snap is not None else {}
    settings = settings or {}
    return DataFacts(
        dataset=ds,
        probes=probes,
        runs=_run_facts(ds, probes, ledger),
        ledger=ledger,
        stake=stake,
        client_of={pk: client_id_of(n) for pk, n in nodes.items()},
        reference_nodes=nodes,
        reference_time_us=ref_snap.taken_unix_us if ref_snap is not None else None,
        proxy_by_family=_proxy_by_family(ds),
        issues=list(ds.issues),
        n_boot=int(settings.get("n_boot") or N_BOOT),
        n_perm=int(settings.get("n_perm") or N_PERM),
    )


def load_context(data_dir: Path | str | None, results_dir: Path | str | None) -> Context:
    results = load_results(results_dir)
    settings = ((results.get("summary") or {}).get("inputs") or {}).get("settings") or {}
    return Context(results=results, data=load_data_facts(data_dir, settings))
