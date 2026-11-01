"""Tidy tables: one row per probe, per probe pair and per block.

Attribution always follows the block's producer (the `Fee` reward pubkey),
never the planned target: a probe that spilled into the next leader's block
is that leader's evidence. Client families come from the gossip snapshot
nearest in time to the window.
"""

from __future__ import annotations

import bisect
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .constants import LEADER_SHARE_PER_SIGNATURE, UNKNOWN_FAMILY, agave_priority, orderer_class
from .load import Dataset, Run, Snapshot
from .orders import OrderSet

# -- snapshots ---------------------------------------------------------------


def family_at(snapshots: list[Snapshot], pubkey: str | None, t_us: float | None) -> str | None:
    """clientId of `pubkey` in the snapshot nearest to `t_us` that lists it."""
    if pubkey is None or not snapshots:
        return None
    listed = [s for s in snapshots if pubkey in s.nodes]
    if not listed:
        return None
    if t_us is None:
        return listed[0].client_id(pubkey)
    nearest = min(listed, key=lambda s: (abs(s.taken_unix_us - t_us), s.taken_unix_us))
    return nearest.client_id(pubkey)


def client_changed(snapshots: list[Snapshot], pubkey: str | None, t_us: float | None) -> bool:
    """Whether `pubkey`'s clientId differs between the snapshots bracketing `t_us`."""
    if pubkey is None or t_us is None:
        return False
    listed = [s for s in snapshots if pubkey in s.nodes]
    before = [s for s in listed if s.taken_unix_us <= t_us]
    after = [s for s in listed if s.taken_unix_us >= t_us]
    if not before or not after:
        return False
    return before[-1].client_id(pubkey) != after[0].client_id(pubkey)


# -- entries -----------------------------------------------------------------


@dataclass
class SlotEntries:
    """Non-tick entries of one slot, sorted by first transaction index."""

    starts: list[int]
    counts: list[int]
    indices: list[int]

    def locate(self, tx_index: int) -> tuple[int, int, int] | None:
        """(entry index, entry size, position inside the entry) of a transaction."""
        k = bisect.bisect_right(self.starts, tx_index) - 1
        if k < 0 or tx_index >= self.starts[k] + self.counts[k]:
            return None
        return self.indices[k], self.counts[k], tx_index - self.starts[k]

    @property
    def sizes(self) -> list[int]:
        return list(self.counts)


def _tiles(starts: list[int], counts: list[int], n_tx: int | None) -> bool:
    nxt = 0
    for s, c in sorted(zip(starts, counts)):
        if s != nxt:
            return False
        nxt = s + c
    return n_tx is None or nxt == n_tx


def clean_entries(lines: Iterable[dict[str, Any]] | None, blocks: dict[int, dict[str, Any]]) -> dict[int, SlotEntries]:
    """Entries per slot, without banks the stream later cleared, ticks or duplicates.

    When a slot still has entries from more than one bank, the bank whose
    entries tile the stored block's transactions is kept (else the latest).
    """
    if not lines:
        return {}
    cleared: dict[int, set[Any]] = {}
    by_slot: dict[int, dict[Any, dict[int, dict[str, Any]]]] = {}
    for e in lines:
        slot = e.get("slot")
        if slot is None:
            continue
        slot = int(slot)
        if "update_parent" in e:
            cb = (e.get("update_parent") or {}).get("cleared_bank_id")
            cleared.setdefault(slot, set()).add(cb)
            continue
        if e.get("starting_transaction_index") is None or e.get("executed_transaction_count") is None:
            continue
        bank = e.get("bank_id")
        by_slot.setdefault(slot, {}).setdefault(bank, {})[int(e.get("index", -1))] = e
    out: dict[int, SlotEntries] = {}
    for slot, banks in by_slot.items():
        live = {b: es for b, es in banks.items() if b not in cleared.get(slot, set())} or banks
        n_tx = len(blocks[slot]["txs"]) if slot in blocks else None
        chosen = None
        for bank in sorted(live, key=lambda b: (b is None, b if b is not None else 0), reverse=True):
            es = [x for x in live[bank].values() if int(x["executed_transaction_count"]) > 0]
            if _tiles([int(x["starting_transaction_index"]) for x in es], [int(x["executed_transaction_count"]) for x in es], n_tx):
                chosen = es
                break
        if chosen is None:
            latest = max(live, key=lambda b: (b is not None, b if b is not None else 0))
            chosen = [x for x in live[latest].values() if int(x["executed_transaction_count"]) > 0]
        chosen.sort(key=lambda x: int(x["starting_transaction_index"]))
        out[slot] = SlotEntries(
            starts=[int(x["starting_transaction_index"]) for x in chosen],
            counts=[int(x["executed_transaction_count"]) for x in chosen],
            indices=[int(x.get("index", i)) for i, x in enumerate(chosen)],
        )
    return out


# -- blocks ------------------------------------------------------------------


class BlockIndex:
    """Signature → ledger index per block, built lazily."""

    def __init__(self, blocks: dict[int, dict[str, Any]]):
        self.blocks = blocks
        self._index: dict[int, dict[str, int]] = {}

    def locate(self, slot: int, sig: str) -> int | None:
        if slot not in self.blocks:
            return None
        if slot not in self._index:
            txs = self.blocks[slot].get("txs", [])
            self._index[slot] = {t.get("sig"): int(t.get("i", k)) for k, t in enumerate(txs)}
        return self._index[slot].get(sig)


def tx_priority(tx: dict[str, Any]) -> float:
    """Agave priority of a foreign transaction, rebuilt from the trimmed block.

    The scheduler prices a transaction on its requested compute; the block
    holds the cost after execution, so the requested limit replaces the
    consumed units when both are known.
    """
    cost = tx.get("cost_units")
    if cost is None:
        return float("nan")
    consumed, limit = tx.get("cu_consumed"), tx.get("cu_limit")
    if consumed is not None and limit is not None:
        cost = cost - consumed + limit
    reward = int(tx.get("priority_fee", 0)) + LEADER_SHARE_PER_SIGNATURE * int(tx.get("n_sigs", 1))
    return reward * 1e6 / (max(cost, 0) + 1)


def _rank(x: np.ndarray) -> np.ndarray:
    return pd.Series(x).rank(method="average").to_numpy()


def block_features(block: dict[str, Any]) -> dict[str, float]:
    """Observational whole-block features over every transaction of the block."""
    txs = block.get("txs", [])
    n = len(txs)
    prio = np.array([tx_priority(t) for t in txs], dtype=float)
    fee = np.array([float(t.get("priority_fee", 0)) for t in txs], dtype=float)
    ok = np.isfinite(prio)
    out = {"n_tx": float(n), "share_zero_priority": float(np.mean(fee == 0)) if n else float("nan")}
    p = prio[ok]
    idx = np.flatnonzero(ok).astype(float)
    if p.size >= 3 and np.ptp(p) > 0:
        out["spearman_priority_index"] = float(np.corrcoef(_rank(p), _rank(idx))[0, 1])
        diff = np.diff(p)
        moving = diff != 0
        out["adjacent_monotonicity"] = float(np.mean(diff[moving] < 0)) if moving.any() else float("nan")
    else:
        out["spearman_priority_index"] = float("nan")
        out["adjacent_monotonicity"] = float("nan")
    return out


# -- tidy --------------------------------------------------------------------


def _priority_of(p: dict[str, Any]) -> float | None:
    """The probe's Agave priority as recorded, or rebuilt from its fee and cost."""
    if p.get("agave_priority") is not None:
        return float(p["agave_priority"])
    if p.get("est_cost") is not None:
        return float(agave_priority(int(p.get("priority_fee", 0)), 1, int(p["est_cost"])))
    return None


def _window_time(window: dict[str, Any], sent: list[int]) -> float | None:
    for key in ("first_send_unix_us", "predicted_window_start_unix_us"):
        if window.get(key) is not None:
            return float(window[key])
    return float(min(sent)) if sent else None


def _probe_rows(run: Run, blocks: dict[int, dict[str, Any]], index: BlockIndex) -> list[dict[str, Any]]:
    batches = {int(b["batch_id"]): b for b in run.batches}
    windows = {int(w["batch_id"]): w for w in run.windows}
    entries = clean_entries(run.entries, blocks)
    sent_by_batch: dict[int, list[int]] = {}
    for p in run.probes:
        if p.get("sent_unix_us") is not None:
            sent_by_batch.setdefault(int(p["batch_id"]), []).append(int(p["sent_unix_us"]))
    rows = []
    for p in run.probes:
        bid = int(p["batch_id"])
        batch = batches.get(bid, {})
        design = batch.get("design", {})
        dprobe = next((q for q in design.get("probes", []) if int(q["idx"]) == int(p["idx"])), {})
        window = windows.get(bid, {})
        t_win = _window_time(window, sent_by_batch.get(bid, []))
        target = batch.get("target", window.get("target"))
        planned_family = batch.get("family", window.get("family"))
        sent = p.get("sent_unix_us")
        first = window.get("first_send_unix_us")
        offset = p.get("planned_offset_us", dprobe.get("send_offset_us", 0)) or 0
        if sent is not None:
            t_send = int(sent)
        elif first is not None:
            t_send = int(first) + int(offset)
        else:
            t_send = int(offset)
        status = run.statuses.get(p.get("signature"))
        landed_slot = int(status["slot"]) if status and status.get("slot") is not None else None
        block = blocks.get(landed_slot) if landed_slot is not None else None
        block_index = index.locate(landed_slot, p.get("signature")) if block is not None else None
        producer = block.get("leader") if block is not None else None
        prod_family = family_at(run.snapshots, producer, t_win)
        if prod_family is None and producer is not None:
            prod_family = planned_family if producer == target else UNKNOWN_FAMILY
        tgt_family = family_at(run.snapshots, target, t_win) or planned_family
        entry = None
        if block_index is not None and landed_slot in entries:
            entry = entries[landed_slot].locate(block_index)
        anchor = design.get("anchor", {}) or {}
        wfs = batch.get("window_first_slot", window.get("window_first_slot"))
        rows.append(
            {
                "run_id": run.run_id,
                "batch_id": bid,
                "window_key": f"{run.run_id}/{bid}",
                "idx": int(p["idx"]),
                "signature": p.get("signature"),
                "payer": p.get("payer"),
                "shared_write": p.get("shared_write"),
                "kind": design.get("kind", window.get("kind")),
                "arm": design.get("arm", window.get("arm")),
                "path": batch.get("path", window.get("path")),
                "anchor_type": anchor.get("type"),
                "anchor_slots": anchor.get("slots"),
                "spacing_us": design.get("spacing_us"),
                "target": target,
                "planned_family": planned_family,
                "target_family": tgt_family,
                "version": batch.get("version", window.get("version")),
                "window_first_slot": wfs,
                "window_status": window.get("status"),
                "window_time_us": t_win,
                "first_send_unix_us": first,
                "slot_us": window.get("slot_us"),
                "tpu_is_proxy": window.get("tpu_is_proxy", batch.get("tpu_is_proxy")),
                "rtt_us": window.get("rtt_us"),
                "priority_fee": int(p.get("priority_fee", dprobe.get("priority_fee", 0))),
                "format": p.get("format", dprobe.get("format")),
                "est_cost": p.get("est_cost"),
                "agave_priority": _priority_of(p),
                "send_rank": int(p.get("send_rank", dprobe.get("send_rank", 0))),
                "planned_offset_us": int(offset),
                "sent_unix_us": sent,
                "sent_done_unix_us": p.get("sent_done_unix_us"),
                "t_send_us": t_send,
                "send_err": p.get("send_err"),
                "sent": sent is not None and p.get("send_err") is None,
                "landed": landed_slot is not None,
                "landed_slot": landed_slot,
                "status_err": (status.get("err") is not None) if status else None,
                "in_block": block_index is not None,
                "block_index": block_index,
                "block_n_tx": len(block.get("txs", [])) if block is not None else None,
                "producer": producer,
                "producer_family": prod_family,
                "producer_class": orderer_class(prod_family),
                "spilled": (producer != target) if producer is not None else None,
                "client_changed": client_changed(run.snapshots, producer, t_win) if producer is not None else None,
                "slot_in_window": (landed_slot - int(wfs)) if (landed_slot is not None and wfs is not None) else None,
                "entries_available": landed_slot in entries if landed_slot is not None else False,
                "entry_index": entry[0] if entry else None,
                "entry_size": entry[1] if entry else None,
                "entry_pos": entry[2] if entry else None,
            }
        )
    return rows


PROBE_INT_COLUMNS = [
    "landed_slot",
    "block_index",
    "block_n_tx",
    "slot_in_window",
    "entry_index",
    "entry_size",
    "entry_pos",
    "window_first_slot",
    "sent_unix_us",
    "sent_done_unix_us",
    "first_send_unix_us",
    "rtt_us",
    "est_cost",
]


def probes_frame(runs: list[Run], blocks: dict[int, dict[str, Any]]) -> pd.DataFrame:
    index = BlockIndex(blocks)
    rows: list[dict[str, Any]] = []
    for run in runs:
        rows.extend(_probe_rows(run, blocks, index))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in PROBE_INT_COLUMNS:
        df[col] = pd.array(df[col].astype("float").round(), dtype="Int64") if df[col].notna().any() else pd.array([pd.NA] * len(df), dtype="Int64")
    df["agave_priority"] = df["agave_priority"].astype("float")
    for col in ("spilled", "client_changed", "status_err"):
        df[col] = df[col].astype("boolean")
    df = df.sort_values(["run_id", "batch_id", "idx"], kind="mergesort").reset_index(drop=True)
    # Rank of each probe among its batch's probes in the same block.
    inb = df["in_block"]
    df["position_in_batch_block"] = pd.array([pd.NA] * len(df), dtype="Int64")
    if inb.any():
        sub = df[inb]
        df.loc[inb, "position_in_batch_block"] = (
            sub.groupby(["run_id", "batch_id", "landed_slot"])["block_index"].rank(method="first").astype(int) - 1
        ).to_numpy()
    return df


def blocks_frame(blocks: dict[int, dict[str, Any]], probes: pd.DataFrame, runs: list[Run]) -> pd.DataFrame:
    """One row per block with its producer, family and observational features."""
    entries: dict[int, SlotEntries] = {}
    for run in runs:
        entries.update(clean_entries(run.entries, blocks))
    fam_by_slot: dict[int, str] = {}
    n_probes: Counter[int] = Counter()
    if not probes.empty:
        landed = probes[probes["in_block"]]
        for slot, grp in landed.groupby("landed_slot"):
            fam_by_slot[int(slot)] = grp["producer_family"].mode().iloc[0]
            n_probes[int(slot)] = len(grp)
    all_snaps = sorted((s for r in runs for s in r.snapshots), key=lambda s: s.taken_unix_us)
    rows = []
    for slot in sorted(blocks):
        b = blocks[slot]
        leader = b.get("leader")
        fam = fam_by_slot.get(slot)
        if fam is None:
            t = b.get("block_time")
            fam = family_at(all_snaps, leader, float(t) * 1e6 if t is not None else None) or UNKNOWN_FAMILY
        row: dict[str, Any] = {"slot": slot, "leader": leader, "family": fam, "orderer_class": orderer_class(fam), "n_probes": n_probes.get(slot, 0)}
        row.update(block_features(b))
        e = entries.get(slot)
        row["entries_available"] = e is not None
        if e is not None and e.counts:
            sizes = np.array(e.counts, dtype=float)
            row.update({"n_entries": float(sizes.size), "mean_entry_size": float(sizes.mean()), "share_single_tx_entries": float(np.mean(sizes == 1))})
        else:
            row.update({"n_entries": float("nan"), "mean_entry_size": float("nan"), "share_single_tx_entries": float("nan")})
        rows.append(row)
    return pd.DataFrame(rows)


def tidy(runs: list[Run], blocks: dict[int, dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(probes_df, pairs_df, blocks_df) for a set of runs and their blocks."""
    probes = probes_frame(runs, blocks)
    pairs = OrderSet.from_probes(probes).pairs_frame() if not probes.empty else pd.DataFrame()
    blocks_df = blocks_frame(blocks, probes, runs)
    return probes, pairs, blocks_df


def tidy_dataset(ds: Dataset) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return tidy(ds.runs, ds.blocks)


def windows_frame(runs: list[Run]) -> pd.DataFrame:
    """One row per planned batch with its window record (skipped ones included)."""
    rows = []
    for run in runs:
        windows = {int(w["batch_id"]): w for w in run.windows}
        for b in run.batches:
            bid = int(b["batch_id"])
            w = windows.get(bid, {})
            rows.append(
                {
                    "run_id": run.run_id,
                    "batch_id": bid,
                    "window_key": f"{run.run_id}/{bid}",
                    "target": b.get("target"),
                    "planned_family": b.get("family"),
                    "kind": b.get("design", {}).get("kind"),
                    "arm": b.get("design", {}).get("arm"),
                    "path": b.get("path"),
                    "status": w.get("status", "missing"),
                }
            )
    return pd.DataFrame(rows)
