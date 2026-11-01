"""Synthetic `data/` trees from validators whose ordering rule is known.

Every synthetic validator orders its blocks by one ground-truth rule:

* `fifo`                 by arrival;
* `priority_fifo_ties`   by priority, equal priority by earliest arrival;
* `priority_lifo_ties`   by priority, equal priority by latest arrival;
* `batched(w, phase)`    arrivals grouped by floor((a − phase) / w), groups in
                         time order, priority inside a group;
* `threads(k)`           a priority pass, then k execution threads: each round
                         deals the next transactions to the threads, every
                         thread's share becomes one entry and the round's
                         entries are recorded in random order. Transactions
                         that write one account stay on one thread, so the
                         shared arm keeps priority order;
* `random`               a uniformly random order.

Arrival = send time + the validator's one-way delay + exponential jitter; each
probe lands with the validator's probability, may slip a slot or spill into
the next leader's block. Blocks also hold foreign transactions ordered by the
same rule. The batch designs follow `design.rs` and the condition cycle of
`plan.rs`; the sweep's fee direction is a parameter so a design change can be
evaluated before it is run. Everything is deterministic in the seed.
"""

from __future__ import annotations

import gzip
import hashlib
import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .constants import (
    BASE_FEE_PER_SIGNATURE,
    FORMAT_LEVELS,
    LADDER8,
    LADDER12,
    LOADED_DATA_COST_PER_32K,
    SIGNATURE_COST,
    SLOTS_PER_WINDOW,
    SWEEP_SPACINGS_US,
    TIE_FEE,
    TIE_SPACINGS_US,
    WRITE_LOCK_COST,
    agave_priority,
)
from .load import Dataset, Run, Snapshot
from .models import rule_order

B58 = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
T0_UNIX_US = 1_792_000_000_000_000
S0 = 463_900_000
WINDOW_GAP_SLOTS = 8

# plan.rs MAIN_CYCLE: (kind, arm, anchor type, anchor slots, path).
MAIN_CYCLE: tuple[tuple[str, str, str, float, str], ...] = (
    ("ladder", "distinct", "before_window", 1.0, "quic"),
    ("ladder", "distinct", "before_window", 1.0, "quic"),
    ("ladder", "shared", "before_window", 1.0, "quic"),
    ("ladder", "shared", "before_window", 1.0, "quic"),
    ("ladder", "distinct", "in_window", 1.5, "quic"),
    ("tie", "distinct", "before_window", 1.0, "quic"),
    ("tie", "shared", "before_window", 1.0, "quic"),
    ("sweep", "distinct", "in_window", 0.5, "quic"),
    ("sweep", "distinct", "in_window", 0.5, "quic"),
    ("format", "distinct", "before_window", 1.0, "quic"),
    ("ladder", "distinct", "before_window", 1.0, "rpc"),
)


@dataclass(frozen=True)
class Rule:
    """A ground-truth ordering rule."""

    name: str
    w_ms: float = 0.0
    phase_ms: float = 0.0
    k: int = 0

    @property
    def label(self) -> str:
        if self.name == "batched":
            return f"batched({self.w_ms:g},{self.phase_ms:g})"
        if self.name == "threads":
            return f"threads({self.k})"
        return self.name

    @property
    def expected_model(self) -> str:
        """The rule model a correct analysis should select."""
        return "priority_fifo_ties" if self.name == "threads" else self.name

    @classmethod
    def parse(cls, text: str) -> "Rule":
        text = text.strip()
        if "(" not in text:
            return cls(text)
        name, args = text[:-1].split("(", 1)
        vals = [float(x) for x in args.split(",") if x.strip()]
        if name == "batched":
            return cls("batched", vals[0], vals[1] if len(vals) > 1 else 0.0)
        if name == "threads":
            return cls("threads", k=int(vals[0]))
        raise ValueError(f"unknown rule {text!r}")


@dataclass
class SimValidator:
    identity: str
    family: str
    rule: Rule
    delay_us: float = 20_000.0
    jitter_us: float = 80.0
    land_p: float = 0.85
    spill_p: float = 0.03
    late_p: float = 0.08
    entry_mode: str = "agave"
    family_after: str | None = None
    tpu_is_proxy: bool = False
    version: str = "4.3.2"
    conditions: list[tuple[str, str, str, float, str]] = field(default_factory=list)


@dataclass
class SimConfig:
    seed: int
    validators: list[SimValidator]
    background: list[SimValidator]
    n_runs: int = 1
    slot_us: float = 213_000.0
    foreign_tx: tuple[int, int] = (60, 200)
    sweep_fee: str = "rising"
    with_entries: bool = True
    with_footers: bool = True
    cu_limit: int = 8_000
    loaded_data_limit: int = 262_144
    payer_pool: int = 16
    preset: str = "custom"


@dataclass
class RunFiles:
    """Contents of every file of one synthetic run directory."""

    run_id: str
    config: dict[str, Any]
    design: dict[str, Any]
    windows: list[dict[str, Any]]
    probes: list[dict[str, Any]]
    statuses: dict[str, Any]
    slots: list[dict[str, Any]]
    entries: list[dict[str, Any]]
    footers: list[dict[str, Any]]
    log: list[dict[str, Any]]
    targets: list[dict[str, Any]]
    collect: dict[str, Any]
    node_snapshots: list[tuple[int, list[dict[str, Any]]]]
    epoch_info: dict[str, Any]


@dataclass
class SimData:
    config: SimConfig
    runs: list[RunFiles]
    blocks: dict[int, dict[str, Any]]
    ledger: list[dict[str, Any]]
    truth: dict[str, Rule]

    def to_dataset(self) -> Dataset:
        """The same data as `load_dataset` would return after `write`."""
        runs = []
        for rf in self.runs:
            snaps = [Snapshot(taken_unix_us=ts, nodes={n["pubkey"]: n for n in nodes}) for ts, nodes in rf.node_snapshots]
            runs.append(
                Run(
                    run_id=rf.run_id,
                    path=None,
                    config=json.loads(json.dumps(rf.config)),
                    design=json.loads(json.dumps(rf.design)),
                    windows=json.loads(json.dumps(rf.windows)),
                    probes=json.loads(json.dumps(rf.probes)),
                    statuses=json.loads(json.dumps(rf.statuses)),
                    slots=rf.slots,
                    entries=(json.loads(json.dumps(rf.entries)) if self.config.with_entries else None),
                    footers=(rf.footers if self.config.with_footers else None),
                    snapshots=sorted(snaps, key=lambda s: s.taken_unix_us),
                )
            )
        return Dataset(runs=runs, blocks=self.blocks)


# -- random identifiers -------------------------------------------------------


def b58_strings(rng: np.random.Generator, n: int, length: int) -> list[str]:
    """`n` random base58 strings of `length` characters."""
    if n == 0:
        return []
    alphabet = np.frombuffer(B58, dtype="S1")
    chars = alphabet[rng.integers(0, 58, size=(n, length))]
    return [s.decode("ascii") for s in chars.view(f"S{length}").ravel()]


def pubkey(rng: np.random.Generator) -> str:
    return b58_strings(rng, 1, 44)[0]


# -- designs (design.rs) -------------------------------------------------------


def probe_est_cost(fmt: str, fee: int, shared: bool, cu_limit: int, loaded: int) -> int:
    """Cost the leader sees for one probe (cost.rs `estimated_cost`)."""
    n_writable = 2 if shared else 1
    data = 12 + (12 if shared else 0)
    if fmt == "legacy":
        data += 5 + 5 + (9 if fee > 0 else 0)
    loaded_cost = math.ceil(loaded / 32_768) * LOADED_DATA_COST_PER_32K
    return SIGNATURE_COST + WRITE_LOCK_COST * n_writable + data // 4 + cu_limit + loaded_cost


def design_batch(kind: str, arm: str, anchor: dict[str, Any], seed: int, payer_pool: int, sweep_fee: str) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    if kind == "ladder":
        fees = list(rng.permutation(LADDER8))
        formats, spacing = ["v1"] * 8, 0
    elif kind == "tie":
        fees, formats = [TIE_FEE] * 8, ["v1"] * 8
        spacing = int(rng.choice(TIE_SPACINGS_US))
    elif kind == "sweep":
        spacing = int(rng.choice(SWEEP_SPACINGS_US))
        fees = sorted(LADDER12, reverse=(sweep_fee == "decreasing"))
        formats = ["v1"] * 12
    elif kind == "format":
        pairs = [(f, fmt) for f in FORMAT_LEVELS for fmt in ("v1", "legacy")]
        order = rng.permutation(len(pairs))
        fees = [pairs[i][0] for i in order]
        formats = [pairs[i][1] for i in order]
        spacing = 0
    else:
        raise ValueError(kind)
    n = len(fees)
    send_rank = list(range(n)) if kind == "sweep" else [int(x) for x in rng.permutation(n)]
    payers = rng.permutation(payer_pool)[:n]
    probes = [
        {
            "idx": i,
            "priority_fee": int(fees[i]),
            "format": formats[i],
            "send_rank": send_rank[i],
            "send_offset_us": send_rank[i] * spacing,
            "payer_slot": int(payers[i]),
        }
        for i in range(n)
    ]
    return {"kind": kind, "arm": arm, "anchor": anchor, "spacing_us": spacing, "seed": int(seed), "probes": probes}


def sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


# -- block building ------------------------------------------------------------


def greedy_schedule(arrival: np.ndarray, priority: np.ndarray, start: float, slot_us: float, lifo: bool) -> np.ndarray:
    """A working leader: at every step the best pending transaction, one per service interval."""
    n = arrival.size
    service = slot_us * 0.9 / max(n, 1)
    by_arrival = np.argsort(arrival, kind="stable")
    heap: list[tuple[int, int, int]] = []
    out: list[int] = []
    t, i = float(start), 0
    while len(out) < n:
        while i < n and arrival[by_arrival[i]] <= t:
            j = int(by_arrival[i])
            heapq.heappush(heap, (-int(priority[j]), -int(arrival[j]) if lifo else int(arrival[j]), j))
            i += 1
        if not heap:
            t = float(arrival[by_arrival[i]])
            continue
        out.append(heapq.heappop(heap)[2])
        t += service
    return np.array(out, dtype=np.int64)


def apply_rule(
    rule: Rule,
    arrival: np.ndarray,
    priority: np.ndarray,
    conflict: np.ndarray,
    is_probe: np.ndarray,
    rng: np.random.Generator,
    entry_mode: str,
    start: float,
    slot_us: float,
) -> tuple[np.ndarray, list[int]]:
    """Block order under `rule`, and the entry sizes.

    Foreign transactions are placed as a working leader would place them
    (priority rules: a greedy pass over what is pending, not one global
    sort); our probes then fill the positions they got, in exactly the
    order the rule prescribes, so the ground truth holds for them exactly.
    """
    n = arrival.size
    seq = np.arange(n)
    if rule.name == "random":
        base = exact = rng.permutation(n)
    elif rule.name in ("priority_fifo_ties", "priority_lifo_ties", "threads"):
        lifo = rule.name == "priority_lifo_ties"
        base = greedy_schedule(arrival, priority, start, slot_us, lifo)
        exact = rule_order("priority_lifo_ties" if lifo else "priority_fifo_ties", arrival, priority, seq)
    elif rule.name == "batched":
        base = exact = rule_order("batched", arrival, priority, seq, w_us=int(rule.w_ms * 1000), phase_us=int(rule.phase_ms * 1000))
    else:
        base = exact = rule_order(rule.name, arrival, priority, seq)
    order = np.asarray(base, dtype=np.int64).copy()
    order[np.flatnonzero(is_probe[order])] = exact[is_probe[exact]]
    if rule.name == "threads":
        return interleave_threads(order, conflict, max(rule.k, 1), rng)
    return order, entry_sizes(n, entry_mode, rng)


def interleave_threads(base: np.ndarray, conflict: np.ndarray, k: int, rng: np.random.Generator, max_chunk: int = 16) -> tuple[np.ndarray, list[int]]:
    order: list[int] = []
    sizes: list[int] = []
    pos = 0
    while pos < base.size:
        chunk = int(rng.integers(1, max_chunk + 1))
        round_txs = base[pos : pos + k * chunk]
        pos += round_txs.size
        threads: list[list[int]] = [[] for _ in range(k)]
        owner: dict[int, int] = {}
        for j, tx in enumerate(round_txs):
            c = int(conflict[tx])
            if c >= 0 and c in owner:
                t = owner[c]
            else:
                t = j % k
                if c >= 0:
                    owner[c] = t
            threads[t].append(int(tx))
        busy = [th for th in threads if th]
        for i in rng.permutation(len(busy)):
            order.extend(busy[i])
            sizes.append(len(busy[i]))
    return np.array(order, dtype=np.int64), sizes


def entry_sizes(n: int, mode: str, rng: np.random.Generator) -> list[int]:
    if mode == "firedancer":
        return [1] * n
    sizes = []
    left = n
    while left > 0:
        s = int(min(left, 1 + rng.poisson(4), 64))
        sizes.append(s)
        left -= s
    return sizes


def foreign_txs(rng: np.random.Generator, n: int, slot_start: float, slot_us: float) -> dict[str, np.ndarray]:
    zero = rng.random(n) < 0.35
    fee = np.where(zero, 0, np.minimum(np.round(rng.lognormal(math.log(3_000), 1.5, n)), 5_000_000)).astype(np.int64)
    n_sigs = np.where(rng.random(n) < 0.9, 1, 2)
    cu_limit = rng.choice(np.array([200_000, 1_400_000, 300_000, 50_000, 25_000]), size=n, p=[0.35, 0.15, 0.15, 0.2, 0.15])
    cu_consumed = (cu_limit * rng.uniform(0.05, 0.9, n)).astype(np.int64)
    n_writable = rng.integers(2, 7, n)
    static = SIGNATURE_COST * n_sigs + WRITE_LOCK_COST * n_writable + rng.integers(50, 500, n) // 4
    loaded = LOADED_DATA_COST_PER_32K * rng.integers(1, 65, n)
    est = static + cu_limit + loaded
    prio = ((fee + 2_500 * n_sigs) * 1_000_000) // (est + 1)
    arrival = rng.uniform(slot_start - slot_us, slot_start + 0.95 * slot_us, n).astype(np.int64)
    return {
        "fee": fee,
        "n_sigs": n_sigs,
        "cu_limit": cu_limit,
        "cu_consumed": cu_consumed,
        "cost_units": static + cu_consumed + loaded,
        "priority": prio,
        "arrival": arrival,
        "n_writable": n_writable,
    }


# -- the generator ---------------------------------------------------------------


@dataclass
class _Probe:
    record: dict[str, Any]
    arrival: int
    slot: int | None
    shared_key: int
    est_cost: int
    cu_consumed: int


def _anchor_offset(anchor: dict[str, Any], slot_us: float) -> int:
    off = int(round(float(anchor["slots"]) * slot_us))
    return -off if anchor["type"] == "before_window" else off


def generate(cfg: SimConfig) -> SimData:
    """Generate a complete synthetic data set from a configuration."""
    rng = np.random.default_rng(cfg.seed)
    slot_us = cfg.slot_us

    def slot_start(s: int) -> float:
        return T0_UNIX_US + (s - S0) * slot_us

    # Schedule every batch of every validator into an 8-slot grid.
    jobs = [(vi, cond) for vi, v in enumerate(cfg.validators) for cond in v.conditions]
    order = rng.permutation(len(jobs))
    jobs = [jobs[i] for i in order]
    n_runs = max(1, min(cfg.n_runs, len(jobs)))
    per_run = math.ceil(len(jobs) / n_runs) if jobs else 0
    first_slot = S0 + 2_000
    window_slots = [first_slot + WINDOW_GAP_SLOTS * k + (k // max(per_run, 1)) * 400 for k in range(len(jobs))]
    background_of = [int(rng.integers(0, len(cfg.background))) for _ in jobs]

    # Gossip view: identities, addresses, clientIds.
    everyone = cfg.validators + cfg.background
    addr = {v.identity: f"10.{i // 250}.{i % 250}.1" for i, v in enumerate(everyone)}

    def nodes_at(after_switch: bool) -> list[dict[str, Any]]:
        out = []
        for v in everyone:
            fam = v.family_after if (after_switch and v.family_after) else v.family
            ip = addr[v.identity]
            tpu_ip = "198.13.140.227" if v.tpu_is_proxy else ip
            out.append(
                {
                    "pubkey": v.identity,
                    "gossip": f"{ip}:8001",
                    "tpuQuic": f"{tpu_ip}:8009",
                    "rpc": None,
                    "version": v.version,
                    "featureSet": 3_294_202_862,
                    "shredVersion": 50_093,
                    "clientId": fam,
                }
            )
        return out

    truth = {v.identity: v.rule for v in everyone}
    runs: list[RunFiles] = []
    ledger: list[dict[str, Any]] = []
    slot_probes: dict[int, list[_Probe]] = {}
    slot_owner: dict[int, SimValidator] = {}
    slot_targeted: set[int] = set()

    for r in range(n_runs):
        chunk = list(range(r * per_run, min((r + 1) * per_run, len(jobs))))
        if not chunk:
            continue
        run_seed = int(rng.integers(0, 2**62))
        start_us = int(slot_start(window_slots[chunk[0]]) - 90_000_000)
        end_us = int(slot_start(window_slots[chunk[-1]] + SLOTS_PER_WINDOW) + 60_000_000)
        mid_us = (start_us + end_us) // 2
        stamp = _run_stamp(start_us)
        run_id = f"{stamp}-{int(rng.integers(0, 0xFFFFFF)):06x}"
        payers = b58_strings(rng, cfg.payer_pool, 44)
        batches, windows, probes, statuses, slots_log, entries, footers = [], [], [], {}, [], [], []
        ledger.append({"ts_unix_us": start_us, "kind": "cap", "run_id": run_id, "purpose": "probe", "batch_id": None, "signature": None, "lamports": 50_000_000})
        for bid, k in enumerate(chunk):
            vi, (kind, arm, anchor_type, anchor_slots, path) = jobs[k]
            v = cfg.validators[vi]
            ws = window_slots[k]
            anchor = {"type": anchor_type, "slots": anchor_slots}
            seed = int(rng.integers(0, 2**63))
            design = design_batch(kind, arm, anchor, seed, cfg.payer_pool, cfg.sweep_fee)
            batch = {
                "batch_id": bid,
                "target": v.identity,
                "family": v.family,
                "version": v.version,
                "tpu_quic": f"{'198.13.140.227' if v.tpu_is_proxy else addr[v.identity]}:8009",
                "tpu_is_proxy": v.tpu_is_proxy,
                "window_first_slot": ws,
                "path": path,
                "design": design,
                "design_hash": sha256_json(design),
            }
            batches.append(batch)
            fit_slot_us = slot_us * (1 + rng.normal(0, 1e-4))
            predicted = int(slot_start(ws) + rng.normal(0, 1_500))
            first = predicted + _anchor_offset(anchor, fit_slot_us)
            shared_sink = pubkey(rng) if arm == "shared" else None
            gap_lo, gap_hi = (20, 60) if path == "quic" else (150, 600)
            sigs = b58_strings(rng, len(design["probes"]), 88)
            by_rank = sorted(design["probes"], key=lambda q: q["send_rank"])
            # Sends go out in send order; a send never starts before the previous one finished.
            sent_at: dict[int, int] = {}
            t_prev: int | None = None
            for q in by_rank:
                planned = first + q["send_offset_us"]
                t_send = planned if t_prev is None else max(planned, t_prev + int(rng.integers(gap_lo, gap_hi + 1)))
                sent_at[q["idx"]] = t_send
                t_prev = t_send
            for q in design["probes"]:
                fmt, fee = q["format"], q["priority_fee"]
                est = probe_est_cost(fmt, fee, shared_sink is not None, cfg.cu_limit, cfg.loaded_data_limit)
                sent = int(sent_at[q["idx"]])
                send_err = "stream reset" if rng.random() < 0.005 else None
                sig = sigs[q["idx"]]
                rec = {
                    "batch_id": bid,
                    "idx": q["idx"],
                    "signature": sig,
                    "payer": payers[q["payer_slot"]],
                    "shared_write": shared_sink,
                    "priority_fee": fee,
                    "format": fmt,
                    "est_cost": est,
                    "agave_priority": agave_priority(fee, 1, est),
                    "send_rank": q["send_rank"],
                    "planned_offset_us": q["send_offset_us"],
                    "sent_unix_us": sent,
                    "sent_done_unix_us": sent + int(rng.integers(40, 400)),
                    "dry_send_unix_us": None,
                    "send_err": send_err,
                    "wire_len": 214 if fmt == "v1" else 268,
                }
                probes.append(rec)
                ledger.append({"ts_unix_us": first - 1_000, "kind": "reserve", "run_id": run_id, "purpose": "probe", "batch_id": bid, "signature": sig, "lamports": BASE_FEE_PER_SIGNATURE + fee})
                extra_delay = rng.uniform(2_000, 15_000) if path == "rpc" else 0.0
                jitter = v.jitter_us * (5 if path == "rpc" else 1)
                arrival = int(sent + v.delay_us + extra_delay + rng.exponential(jitter))
                land_p = v.land_p * (0.9 if path == "rpc" else 1.0)
                landed = send_err is None and rng.random() < land_p
                slot = None
                if landed:
                    k_slot = int(math.floor((arrival - slot_start(ws)) / slot_us))
                    k_slot = max(k_slot, 0)
                    if rng.random() < v.late_p:
                        k_slot += 1
                    if rng.random() < v.spill_p or k_slot >= SLOTS_PER_WINDOW:
                        k_slot = SLOTS_PER_WINDOW
                    slot = ws + k_slot
                    owner = v if k_slot < SLOTS_PER_WINDOW else cfg.background[background_of[k]]
                    slot_owner[slot] = owner
                    shared_key = (bid * 1_000 + r) if shared_sink else -1
                    slot_probes.setdefault(slot, []).append(
                        _Probe(rec, arrival, slot, shared_key, est, 160 + (150 if shared_sink else 0) + int(rng.integers(0, 20)))
                    )
                if send_err is None:
                    statuses[sig] = {"slot": slot, "err": None} if slot is not None else None
                ledger.append({"ts_unix_us": end_us, "kind": "settle", "run_id": run_id, "purpose": "probe", "batch_id": bid, "signature": sig, "lamports": (BASE_FEE_PER_SIGNATURE + fee) if slot is not None else 0})
            for s in range(ws, ws + SLOTS_PER_WINDOW):
                slot_targeted.add(s)
                slot_owner.setdefault(s, v)
                slots_log.append({"slot": s, "status": "processed", "source": "ws", "recv_unix_us": int(slot_start(s) + v.delay_us + 400_000)})
            windows.append(
                {
                    "batch_id": bid,
                    "target": v.identity,
                    "family": v.family,
                    "version": v.version,
                    "window_first_slot": ws,
                    "tpu_quic": batch["tpu_quic"],
                    "tpu_is_proxy": v.tpu_is_proxy,
                    "path": path,
                    "kind": kind,
                    "arm": arm,
                    "slot_us": fit_slot_us,
                    "clock_resid_p95_us": float(abs(rng.normal(2_000, 300))),
                    "predicted_window_start_unix_us": predicted,
                    "first_send_unix_us": first,
                    "highest_slot_seen_at_send": ws - 2,
                    "rtt_us": int(2 * v.delay_us + rng.integers(0, 2_000)) if path == "quic" else None,
                    "handshake_us": int(4 * v.delay_us) if path == "quic" else None,
                    "blockhash": b58_strings(rng, 1, 44)[0],
                    "blockhash_slot": ws - 20,
                    "status": "sent",
                }
            )
        design_doc = {"run_id": run_id, "run_seed": run_seed, "sha256": sha256_json(batches), "batches": batches}
        snapshots = [(start_us, nodes_at(False)), (mid_us, nodes_at(False)), (end_us, nodes_at(True))]
        run_targets = sorted({b["target"] for b in batches})
        runs.append(
            RunFiles(
                run_id=run_id,
                config=_config(cfg, run_id, run_seed, start_us, end_us),
                design=design_doc,
                windows=windows,
                probes=probes,
                statuses=statuses,
                slots=slots_log,
                entries=entries,
                footers=footers,
                log=[
                    {"ts_unix_us": start_us, "level": "info", "msg": "design written before first send", "batches": len(batches)},
                    {"ts_unix_us": end_us, "level": "info", "msg": "collected"},
                ],
                targets=[{"identity": t, "family": next(v.family for v in cfg.validators if v.identity == t)} for t in run_targets],
                collect={"probes_sent": len(statuses), "landed": sum(1 for s in statuses.values() if s), "missing": []},
                node_snapshots=snapshots,
                epoch_info={"absoluteSlot": window_slots[chunk[0]] - 400, "epoch": 1_073, "slotIndex": 1_000, "slotsInEpoch": 432_000},
            )
        )

    # Build every block that holds a probe, with foreign transactions.
    blocks: dict[int, dict[str, Any]] = {}
    run_of_slot: dict[int, int] = {}
    for ri, rf in enumerate(runs):
        for w in rf.windows:
            for s in range(w["window_first_slot"] - 4, w["window_first_slot"] + 8):
                run_of_slot[s] = ri
    for slot in sorted(slot_probes):
        owner = slot_owner[slot]
        blocks[slot], entry_list = _build_block(rng, cfg, slot, slot_start(slot), owner, slot_probes[slot])
        rf = runs[run_of_slot[slot]]
        if cfg.with_entries:
            rf.entries.extend(_entry_lines(rng, slot, slot_start(slot), slot_us, entry_list))
        if cfg.with_footers:
            ua = "firedancer/0.708.20306" if owner.entry_mode == "firedancer" else f"agave/{owner.version}"
            rf.footers.append(
                {"slot": slot, "bank_id": slot * 10, "producer_time_ns": int((slot_start(slot) + 0.9 * slot_us) * 1_000), "user_agent": ua, "created_at_unix_us": int(slot_start(slot) + slot_us), "recv_unix_us": int(slot_start(slot) + slot_us + 3_000)}
            )
    return SimData(config=cfg, runs=runs, blocks=blocks, ledger=ledger, truth=truth)


def _run_stamp(unix_us: int) -> str:
    import datetime as _dt

    t = _dt.datetime.fromtimestamp(unix_us / 1e6, tz=_dt.timezone.utc)
    return t.strftime("%Y%m%dT%H%MZ")


def _config(cfg: SimConfig, run_id: str, run_seed: int, start_us: int, end_us: int) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "mode": "live",
        "live_refusal": None,
        "run_seed": run_seed,
        "rpc_url": "http://127.0.0.1:8899",
        "ws_url": "ws://127.0.0.1:8900",
        "grpc_url": "http://127.0.0.1:10000" if cfg.with_entries else None,
        "cap_lamports": 50_000_000,
        "cu_limit": cfg.cu_limit,
        "loaded_data_limit": cfg.loaded_data_limit,
        "ladder8": list(LADDER8),
        "ladder12": list(LADDER12),
        "tie_fee": TIE_FEE,
        "format_levels": list(FORMAT_LEVELS),
        "families": sorted({v.family for v in cfg.validators}),
        "per_family": 2,
        "batches_per_target": max((len(v.conditions) for v in cfg.validators), default=0),
        "horizon_slots": 9_000,
        "lead_slots": 30,
        "payer_pool": cfg.payer_pool,
        "pilot": False,
        "git_commit": "synthetic",
        "started_unix_us": start_us,
        "ended_unix_us": end_us,
        "master": "Synthetic11111111111111111111111111111111111",
        "synthetic_preset": cfg.preset,
        "synthetic_seed": cfg.seed,
    }


def _build_block(
    rng: np.random.Generator,
    cfg: SimConfig,
    slot: int,
    start: float,
    owner: SimValidator,
    probes: list[_Probe],
) -> tuple[dict[str, Any], list[int]]:
    n_f = int(rng.integers(cfg.foreign_tx[0], cfg.foreign_tx[1] + 1))
    f = foreign_txs(rng, n_f, start, cfg.slot_us)
    n_p = len(probes)
    arrival = np.concatenate([f["arrival"], np.array([p.arrival for p in probes], dtype=np.int64)])
    priority = np.concatenate([f["priority"], np.array([p.record["agave_priority"] for p in probes], dtype=np.int64)])
    conflict = np.concatenate([np.full(n_f, -1, dtype=np.int64), np.array([p.shared_key for p in probes], dtype=np.int64)])
    is_probe = np.concatenate([np.zeros(n_f, dtype=bool), np.ones(n_p, dtype=bool)])
    order, sizes = apply_rule(owner.rule, arrival, priority, conflict, is_probe, rng, owner.entry_mode, start, cfg.slot_us)
    foreign_sigs = b58_strings(rng, n_f, 88)
    foreign_payers = b58_strings(rng, n_f, 44)
    txs = []
    for i, j in enumerate(order):
        j = int(j)
        if j < n_f:
            txs.append(
                {
                    "i": i,
                    "sig": foreign_sigs[j],
                    "version": "0" if j % 3 == 0 else "legacy",
                    "fee": int(BASE_FEE_PER_SIGNATURE * f["n_sigs"][j] + f["fee"][j]),
                    "cost_units": int(f["cost_units"][j]),
                    "cu_consumed": int(f["cu_consumed"][j]),
                    "priority_fee": int(f["fee"][j]),
                    "cu_limit": int(f["cu_limit"][j]),
                    "n_sigs": int(f["n_sigs"][j]),
                    "payer": foreign_payers[j],
                    "writable": [foreign_payers[j]],
                    "err": bool(j % 17 == 0),
                }
            )
        else:
            p = probes[j - n_f]
            rec = p.record
            writable = [rec["payer"]] + ([rec["shared_write"]] if rec["shared_write"] else [])
            txs.append(
                {
                    "i": i,
                    "sig": rec["signature"],
                    "version": "1" if rec["format"] == "v1" else "legacy",
                    "fee": BASE_FEE_PER_SIGNATURE + rec["priority_fee"],
                    "cost_units": rec["est_cost"] - cfg.cu_limit + p.cu_consumed,
                    "cu_consumed": p.cu_consumed,
                    "priority_fee": rec["priority_fee"],
                    "cu_limit": cfg.cu_limit,
                    "n_sigs": 1,
                    "payer": rec["payer"],
                    "writable": writable,
                    "err": False,
                }
            )
    block = {
        "slot": slot,
        "blockhash": b58_strings(rng, 1, 44)[0],
        "previous_blockhash": b58_strings(rng, 1, 44)[0],
        "parent_slot": slot - 1,
        "block_time": int(start // 1_000_000),
        "block_height": slot - 22_000_000,
        "leader": owner.identity,
        "raw_sha256": hashlib.sha256(f"{cfg.seed}:{slot}".encode()).hexdigest(),
        "txs": txs,
    }
    return block, sizes


def _entry_lines(rng: np.random.Generator, slot: int, start: float, slot_us: float, sizes: list[int]) -> list[dict[str, Any]]:
    lines = []
    index = 0
    tx = 0
    n_entries = len(sizes)
    tick_every = max(1, n_entries // 8)
    bank = slot * 10
    for k, size in enumerate(sizes):
        recv = int(start + slot_us * (k + 1) / (n_entries + 1) + 3_000)
        lines.append(_entry(slot, index, tx, size, recv, bank, rng))
        index += 1
        tx += size
        if (k + 1) % tick_every == 0:
            lines.append(_entry(slot, index, tx, 0, recv + 50, bank, rng))
            index += 1
    return lines


def _entry(slot: int, index: int, start_tx: int, count: int, recv: int, bank: int, rng: np.random.Generator) -> dict[str, Any]:
    return {
        "slot": slot,
        "index": index,
        "starting_transaction_index": start_tx,
        "executed_transaction_count": count,
        "num_hashes": int(rng.integers(1, 12_500)),
        "hash": b58_strings(rng, 1, 44)[0],
        "bank_id": bank,
        "created_at_unix_us": recv - 1_000,
        "recv_unix_us": recv,
    }


# -- writing -----------------------------------------------------------------------


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8", newline="\n")


def _dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def write(data: SimData, out_dir: Path | str) -> Path:
    """Write the synthetic data as a `data/` tree (docs/DATA.md) under `out_dir`."""
    out = Path(out_dir)
    for rf in data.runs:
        rd = out / "runs" / rf.run_id
        _dump(rd / "config.json", rf.config)
        _dump(rd / "design.json", rf.design)
        _dump(rd / "targets.json", rf.targets)
        _dump(rd / "statuses.json", rf.statuses)
        _dump(rd / "collect.json", rf.collect)
        _dump_jsonl(rd / "windows.jsonl", rf.windows)
        _dump_jsonl(rd / "probes.jsonl", rf.probes)
        _dump_jsonl(rd / "slots.jsonl", rf.slots)
        _dump_jsonl(rd / "log.jsonl", rf.log)
        if data.config.with_entries:
            _dump_jsonl(rd / "entries.jsonl", rf.entries)
        if data.config.with_footers:
            _dump_jsonl(rd / "footers.jsonl", rf.footers)
        snap = rd / "snapshot"
        for ts, nodes in rf.node_snapshots:
            _dump(snap / f"nodes-{ts}.json", nodes)
        _dump(snap / "epoch_info.json", rf.epoch_info)
        _dump(snap / "features.json", {"raw": [], "parsed": {"alpenglow_activated_slot": 459_648_000, "fast_leader_handover_activated_slot": None}})
    blocks_dir = out / "blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)
    for slot, block in sorted(data.blocks.items()):
        raw = json.dumps(block, separators=(",", ":")).encode()
        with (blocks_dir / f"{slot}.json.gz").open("wb") as fh:
            with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
                gz.write(raw)
    _dump_jsonl(out / "ledger" / "spend.jsonl", data.ledger)
    truth = {
        "preset": data.config.preset,
        "seed": data.config.seed,
        "sweep_fee": data.config.sweep_fee,
        "validators": {
            v.identity: {"family": v.family, "family_after": v.family_after, "rule": v.rule.label, "expected_model": v.rule.expected_model}
            for v in data.config.validators + data.config.background
        },
    }
    _dump(out / "synthetic_truth.json", truth)
    return out


# -- presets ---------------------------------------------------------------------


RULE_FAMILY = {
    "fifo": ("HarmonicAgave", "agave", True),
    "priority_fifo_ties": ("Agave", "agave", False),
    "priority_lifo_ties": ("Firedancer", "firedancer", False),
    "batched": ("AgaveBam", "agave", True),
    "threads": ("JitoLabs", "agave", False),
    "random": ("Rakurai", "agave", False),
}


def main_cycle_conditions(n_batches: int, rng: np.random.Generator) -> list[tuple[str, str, str, float, str]]:
    """plan.rs: the condition cycle in a fresh random order per repetition."""
    out: list[tuple[str, str, str, float, str]] = []
    while len(out) < n_batches:
        out.extend(MAIN_CYCLE[i] for i in rng.permutation(len(MAIN_CYCLE)))
    return out[:n_batches]


def balanced_conditions(rng: np.random.Generator, lo: int = 6, hi: int = 12) -> list[tuple[str, str, str, float, str]]:
    """lo..hi windows of every kind, each drawn from that kind's plan.rs conditions."""
    out = []
    for kind in ("ladder", "tie", "sweep", "format"):
        options = [c for c in MAIN_CYCLE if c[0] == kind]
        for _ in range(int(rng.integers(lo, hi + 1))):
            out.append(options[int(rng.integers(0, len(options)))])
    return out


def _validator(rng: np.random.Generator, rule: Rule, family: str, entry_mode: str, proxy: bool, conditions: list[Any], land: tuple[float, float] = (0.70, 0.95)) -> SimValidator:
    return SimValidator(
        identity=pubkey(rng),
        family=family,
        rule=rule,
        delay_us=float(rng.uniform(2_000, 60_000)),
        jitter_us=float(rng.uniform(30, 200)),
        land_p=float(rng.uniform(*land)),
        spill_p=0.03,
        late_p=0.08,
        entry_mode=entry_mode,
        tpu_is_proxy=proxy,
        conditions=conditions,
    )


def _random_rule(name: str, rng: np.random.Generator) -> Rule:
    if name == "batched":
        w = float(rng.choice([20, 30, 40, 50]))
        return Rule("batched", w, float(np.round(rng.uniform(0, w), 1)))
    if name == "threads":
        return Rule("threads", k=4)
    return Rule(name)


def _background(rng: np.random.Generator, n: int = 4) -> list[SimValidator]:
    return [
        _validator(rng, Rule("priority_fifo_ties"), "Agave", "agave", False, [])
        for _ in range(n)
    ]


def preset_validation(seed: int, reps: int = 6, sweep_fee: str = "rising", foreign: tuple[int, int] = (20, 60)) -> SimConfig:
    """`reps` validators per rule, 6–12 windows per kind, 70–95% landing."""
    rng = np.random.default_rng(seed)
    validators = []
    for name in ("fifo", "priority_fifo_ties", "priority_lifo_ties", "batched", "threads", "random"):
        family, mode, proxy = RULE_FAMILY[name]
        for _ in range(reps):
            validators.append(_validator(rng, _random_rule(name, rng), family, mode, proxy, balanced_conditions(rng)))
    return SimConfig(seed=seed, validators=validators, background=_background(rng), foreign_tx=foreign, sweep_fee=sweep_fee, preset=f"validation-{sweep_fee}")


FAMILY_RULES: tuple[tuple[str, str, str, bool], ...] = (
    ("Agave", "threads", "agave", False),
    ("Agave", "priority_fifo_ties", "agave", False),
    ("JitoLabs", "threads", "agave", False),
    ("JitoLabs", "threads", "agave", False),
    ("Rakurai", "priority_fifo_ties", "agave", False),
    ("Rakurai", "priority_fifo_ties", "agave", False),
    ("Firedancer", "priority_lifo_ties", "firedancer", False),
    ("Firedancer", "priority_lifo_ties", "firedancer", False),
    ("AgaveBam", "batched", "agave", True),
    ("AgaveBam", "batched", "agave", True),
    ("FireBAM", "batched", "firedancer", True),
    ("FireBAM", "batched", "firedancer", True),
    ("HarmonicAgave", "batched", "agave", True),
    ("HarmonicAgave", "fifo", "agave", True),
    ("HarmonicFiredancer", "batched", "firedancer", True),
    ("HarmonicFiredancer", "random", "firedancer", True),
)


def preset_families(seed: int, batches_per_target: int = 22, sweep_fee: str = "rising") -> SimConfig:
    """Two validators per family with source-derived rules, the plan.rs cycle, two runs."""
    rng = np.random.default_rng(seed)
    validators = []
    for family, name, mode, proxy in FAMILY_RULES:
        rule = Rule("batched", 50.0, float(np.round(rng.uniform(0, 50), 1))) if (family.startswith("Harmonic") and name == "batched") else _random_rule(name, rng)
        validators.append(_validator(rng, rule, family, mode, proxy, main_cycle_conditions(batches_per_target, rng)))
    validators[8].family_after = "JitoLabs"  # an AgaveBam validator loses its BAM connection late in the run
    return SimConfig(seed=seed, validators=validators, background=_background(rng), n_runs=2, foreign_tx=(100, 300), sweep_fee=sweep_fee, preset="families")


def preset_small(seed: int) -> SimConfig:
    """One validator per rule and a few windows of each kind: a fast fixture."""
    rng = np.random.default_rng(seed)
    validators = []
    for name in ("fifo", "priority_fifo_ties", "priority_lifo_ties", "batched", "threads", "random"):
        family, mode, proxy = RULE_FAMILY[name]
        validators.append(_validator(rng, _random_rule(name, rng), family, mode, proxy, balanced_conditions(rng, 2, 3)))
    validators[1].family_after = "JitoLabs"
    return SimConfig(seed=seed, validators=validators, background=_background(rng, 2), foreign_tx=(10, 30), preset="small")


PRESETS = {"validation": preset_validation, "families": preset_families, "small": preset_small}


def simulate(out_dir: Path | str, seed: int, preset: str = "validation") -> SimData:
    """Generate a preset and write it under `out_dir`."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    data = generate(PRESETS[preset](seed))
    write(data, out_dir)
    return data
