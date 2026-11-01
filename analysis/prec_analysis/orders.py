"""Observed orders of our probes, flattened to pairs.

The unit of observation is a *batch-block*: the probes of one batch that
landed in one block, at least two of them. Within it the block gives a strict
order (ledger index). Every comparison the analysis makes (rule models, pair
rates, Kendall τ, permutation nulls) is a function of the pairwise signs
"does probe a precede probe b", so `OrderSet` stores exactly those, plus the
per-probe and per-batch-block attributes the comparisons need.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .mallows import sample_orders

ITEM_COLUMNS = [
    "run_id",
    "batch_id",
    "window_key",
    "idx",
    "landed_slot",
    "block_index",
    "agave_priority",
    "priority_fee",
    "t_send_us",
    "send_rank",
    "format",
    "entry_index",
    "entry_size",
]
BB_COLUMNS = [
    "run_id",
    "batch_id",
    "window_key",
    "landed_slot",
    "producer",
    "producer_family",
    "producer_class",
    "target",
    "kind",
    "arm",
    "path",
    "spacing_us",
]


@dataclass
class OrderSet:
    """Batch-blocks, their probes (items) and every within-batch-block pair."""

    bbs: pd.DataFrame
    items: pd.DataFrame
    item_offset: np.ndarray
    pair_bb: np.ndarray
    pair_a: np.ndarray
    pair_b: np.ndarray
    obs: np.ndarray

    # -- construction -----------------------------------------------------
    @classmethod
    def from_probes(cls, probes: pd.DataFrame) -> "OrderSet":
        """Build from the tidy probe table: landed probes with a known block index."""
        sel = probes[probes["in_block"].fillna(False).astype(bool)].copy()
        key = ["run_id", "batch_id", "landed_slot"]
        if len(sel):
            sizes = sel.groupby(key)["idx"].transform("size")
            sel = sel[sizes >= 2]
        sel = sel.sort_values(key + ["idx"], kind="mergesort").reset_index(drop=True)
        if len(sel) == 0:
            return cls.empty()
        bb = sel.groupby(key, sort=False).ngroup().to_numpy()
        sel["bb"] = bb
        n_bb = int(bb.max()) + 1
        counts = np.bincount(bb, minlength=n_bb)
        offset = np.concatenate(([0], np.cumsum(counts)))
        first = offset[:-1]
        bbs = sel.iloc[first][[c for c in BB_COLUMNS if c in sel.columns]].reset_index(drop=True)
        bbs["size"] = counts
        bbs["cluster"] = pd.factorize(bbs["window_key"], sort=True)[0]
        items = sel[[c for c in ITEM_COLUMNS if c in sel.columns] + ["bb"]].reset_index(drop=True)
        pair_bb, pair_a, pair_b = _all_pairs(offset)
        pos = items["block_index"].to_numpy(dtype=np.int64)
        obs = np.sign(pos[pair_b] - pos[pair_a]).astype(np.int8)
        return cls(bbs=bbs, items=items, item_offset=offset, pair_bb=pair_bb, pair_a=pair_a, pair_b=pair_b, obs=obs)

    @classmethod
    def empty(cls) -> "OrderSet":
        z = np.zeros(0, dtype=np.int64)
        return cls(
            bbs=pd.DataFrame(columns=BB_COLUMNS + ["size", "cluster"]),
            items=pd.DataFrame(columns=ITEM_COLUMNS + ["bb"]),
            item_offset=np.zeros(1, dtype=np.int64),
            pair_bb=z,
            pair_a=z,
            pair_b=z,
            obs=np.zeros(0, dtype=np.int8),
        )

    # -- sizes ------------------------------------------------------------
    @property
    def n_bb(self) -> int:
        return len(self.bbs)

    @property
    def n_pairs(self) -> int:
        return int(self.pair_bb.size)

    @property
    def sizes(self) -> np.ndarray:
        return np.diff(self.item_offset)

    # -- per-item helpers -------------------------------------------------
    def column(self, name: str, dtype: type = float) -> np.ndarray:
        return self.items[name].to_numpy(dtype=dtype)

    def ranks_from_order(self, order: np.ndarray) -> np.ndarray:
        """Within-batch-block rank of every item, from a global sort order."""
        bb = self.items["bb"].to_numpy()
        rank = np.empty(len(order), dtype=np.int64)
        rank[order] = np.arange(len(order)) - self.item_offset[bb[order]]
        return rank

    def signs_from_rank(self, rank: np.ndarray) -> np.ndarray:
        """+1 where a model ranks a before b, −1 otherwise."""
        return np.sign(rank[self.pair_b] - rank[self.pair_a]).astype(np.int8)

    def key_sign(self, values: np.ndarray) -> np.ndarray:
        """sign(value_a − value_b): +1 when a has the larger value, 0 on ties."""
        v = np.asarray(values, dtype=float)
        return np.sign(v[self.pair_a] - v[self.pair_b]).astype(np.int8)

    # -- subsets ----------------------------------------------------------
    def pairs_of(self, bb_ids: np.ndarray) -> np.ndarray:
        """Global pair indices of the given batch-blocks, ascending."""
        mask = np.zeros(self.n_bb, dtype=bool)
        mask[np.asarray(bb_ids, dtype=np.int64)] = True
        return np.flatnonzero(mask[self.pair_bb])

    def subset(self, bb_mask: np.ndarray) -> "OrderSet":
        """A new OrderSet holding only the selected batch-blocks."""
        bb_mask = np.asarray(bb_mask, dtype=bool)
        keep = np.flatnonzero(bb_mask)
        if keep.size == 0:
            return OrderSet.empty()
        item_mask = bb_mask[self.items["bb"].to_numpy()]
        items = self.items[item_mask].copy()
        remap = -np.ones(self.n_bb, dtype=np.int64)
        remap[keep] = np.arange(keep.size)
        items["bb"] = remap[items["bb"].to_numpy()]
        items = items.reset_index(drop=True)
        counts = np.bincount(items["bb"].to_numpy(), minlength=keep.size)
        offset = np.concatenate(([0], np.cumsum(counts)))
        bbs = self.bbs.iloc[keep].reset_index(drop=True)
        bbs["cluster"] = pd.factorize(bbs["window_key"], sort=True)[0]
        pair_bb, pair_a, pair_b = _all_pairs(offset)
        pos = items["block_index"].to_numpy(dtype=np.int64)
        obs = np.sign(pos[pair_b] - pos[pair_a]).astype(np.int8)
        return OrderSet(bbs=bbs, items=items, item_offset=offset, pair_bb=pair_bb, pair_a=pair_a, pair_b=pair_b, obs=obs)

    # -- nulls ------------------------------------------------------------
    def permuted_obs(self, rng: np.random.Generator, n: int, bb_ids: np.ndarray | None = None) -> np.ndarray:
        """Pair signs under uniformly random within-batch-block orders.

        Returns an (n, P_sub) int8 array aligned with `pairs_of(bb_ids)` (all
        pairs when `bb_ids` is None).
        """
        if bb_ids is None:
            bb_ids = np.arange(self.n_bb)
        bb_ids = np.sort(np.asarray(bb_ids, dtype=np.int64))
        pairs = self.pairs_of(bb_ids)
        out = np.zeros((n, pairs.size), dtype=np.int8)
        if pairs.size == 0:
            return out
        sizes = self.sizes
        p_bb = self.pair_bb[pairs]
        la = self.pair_a[pairs] - self.item_offset[p_bb]
        lb = self.pair_b[pairs] - self.item_offset[p_bb]
        for m in np.unique(sizes[bb_ids]):
            group = bb_ids[sizes[bb_ids] == m]
            slot_of = -np.ones(self.n_bb, dtype=np.int64)
            slot_of[group] = np.arange(group.size)
            sel = np.flatnonzero(slot_of[p_bb] >= 0)
            base = np.broadcast_to(np.arange(m), (n, group.size, m))
            perm = rng.permuted(base, axis=2)
            g = slot_of[p_bb[sel]]
            out[:, sel] = np.sign(perm[:, g, lb[sel]] - perm[:, g, la[sel]]).astype(np.int8)
        return out

    def mallows_obs(self, rng: np.random.Generator, n: int, bb_ids: np.ndarray, ranks: np.ndarray, theta: float) -> np.ndarray:
        """Pair signs of `n` simulated data sets: Mallows(θ) around the given ranks.

        `ranks` holds a reference rank for every item (0 = first in its
        batch-block). Returns (n, P_sub) aligned with `pairs_of(bb_ids)`.
        """
        bb_ids = np.sort(np.asarray(bb_ids, dtype=np.int64))
        pairs = self.pairs_of(bb_ids)
        out = np.zeros((n, pairs.size), dtype=np.int8)
        if pairs.size == 0:
            return out
        sizes = self.sizes
        p_bb = self.pair_bb[pairs]
        la = self.pair_a[pairs] - self.item_offset[p_bb]
        lb = self.pair_b[pairs] - self.item_offset[p_bb]
        for m in np.unique(sizes[bb_ids]):
            group = bb_ids[sizes[bb_ids] == m]
            slot_of = -np.ones(self.n_bb, dtype=np.int64)
            slot_of[group] = np.arange(group.size)
            sel = np.flatnonzero(slot_of[p_bb] >= 0)
            local = self.item_offset[group][:, None] + np.arange(m)[None, :]
            ref_order = np.argsort(ranks[local], axis=1, kind="stable")  # local item at each reference rank
            tau = sample_orders(rng, theta, (n, group.size), int(m))  # reference rank placed k-th
            item_at = np.take_along_axis(np.broadcast_to(ref_order, tau.shape), tau, axis=2)
            pos = np.empty_like(item_at)
            np.put_along_axis(pos, item_at, np.broadcast_to(np.arange(m), tau.shape), axis=2)
            g = slot_of[p_bb[sel]]
            out[:, sel] = np.sign(pos[:, g, lb[sel]] - pos[:, g, la[sel]]).astype(np.int8)
        return out

    def with_positions(self, positions: np.ndarray) -> "OrderSet":
        """Same structure, observed signs recomputed from new item positions."""
        pos = np.asarray(positions, dtype=np.int64)
        obs = np.sign(pos[self.pair_b] - pos[self.pair_a]).astype(np.int8)
        items = self.items.copy()
        items["block_index"] = pos
        return OrderSet(
            bbs=self.bbs,
            items=items,
            item_offset=self.item_offset,
            pair_bb=self.pair_bb,
            pair_a=self.pair_a,
            pair_b=self.pair_b,
            obs=obs,
        )

    # -- tables -----------------------------------------------------------
    def pairs_frame(self) -> pd.DataFrame:
        """One row per pair with design and outcome columns (the tidy pairs table)."""
        if self.n_pairs == 0:
            return pd.DataFrame()
        it = self.items
        a, b = self.pair_a, self.pair_b
        bbs = self.bbs.iloc[self.pair_bb].reset_index(drop=True)
        prio = it["agave_priority"].to_numpy(dtype=float)
        fee = it["priority_fee"].to_numpy(dtype=float)
        t = it["t_send_us"].to_numpy(dtype=float)
        rank = it["send_rank"].to_numpy(dtype=float)
        fmt = it["format"].astype(str).to_numpy()
        a_first = self.obs > 0
        prio_diff = prio[a] - prio[b]
        send_diff = t[a] - t[b]
        hpf = np.where(prio_diff == 0, np.nan, ((prio_diff > 0) == a_first).astype(float))
        esf = np.where(send_diff == 0, np.nan, ((send_diff < 0) == a_first).astype(float))
        hp_lag = np.where(prio_diff > 0, t[a] - t[b], np.where(prio_diff < 0, t[b] - t[a], np.nan))
        fa, fb = fmt[a], fmt[b]
        format_pair = np.where(fa <= fb, np.char.add(np.char.add(fa, "-"), fb), np.char.add(np.char.add(fb, "-"), fa))
        frame = pd.DataFrame(
            {
                "run_id": bbs["run_id"],
                "batch_id": bbs["batch_id"],
                "window_key": bbs["window_key"],
                "slot": bbs["landed_slot"],
                "validator": bbs["producer"],
                "family": bbs["producer_family"],
                "orderer_class": bbs["producer_class"],
                "kind": bbs["kind"],
                "arm": bbs["arm"],
                "path": bbs["path"],
                "idx_a": it["idx"].to_numpy()[a],
                "idx_b": it["idx"].to_numpy()[b],
                "fee_a": fee[a],
                "fee_b": fee[b],
                "fee_diff": fee[a] - fee[b],
                "priority_diff": prio_diff,
                "send_diff_us": send_diff,
                "send_rank_diff": rank[a] - rank[b],
                "abs_send_gap_us": np.abs(send_diff),
                "hp_send_lag_us": hp_lag,
                "format_pair": format_pair,
                "a_first": a_first,
                "higher_priority_first": pd.array(hpf, dtype="Float64").astype("boolean"),
                "earlier_sent_first": pd.array(esf, dtype="Float64").astype("boolean"),
            }
        )
        if "entry_index" in it.columns:
            ea = it["entry_index"].to_numpy(dtype=float)
            same = np.where(np.isnan(ea[a]) | np.isnan(ea[b]), np.nan, (ea[a] == ea[b]).astype(float))
            frame["same_entry"] = pd.array(same, dtype="Float64").astype("boolean")
        else:
            frame["same_entry"] = pd.array([pd.NA] * len(frame), dtype="boolean")
        return frame


def _all_pairs(offset: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every (bb, a, b) with a < b inside each batch-block, sorted by (bb, a, b)."""
    counts = np.diff(offset)
    parts_bb, parts_a, parts_b = [], [], []
    for m in np.unique(counts):
        if m < 2:
            continue
        group = np.flatnonzero(counts == m)
        iu, ju = np.triu_indices(int(m), 1)
        base = offset[group][:, None]
        parts_bb.append(np.repeat(group, iu.size))
        parts_a.append((base + iu[None, :]).ravel())
        parts_b.append((base + ju[None, :]).ravel())
    if not parts_bb:
        z = np.zeros(0, dtype=np.int64)
        return z, z, z
    pb = np.concatenate(parts_bb)
    pa = np.concatenate(parts_a)
    pbb = np.concatenate(parts_b)
    order = np.lexsort((pbb, pa, pb))
    return pb[order].astype(np.int64), pa[order].astype(np.int64), pbb[order].astype(np.int64)
