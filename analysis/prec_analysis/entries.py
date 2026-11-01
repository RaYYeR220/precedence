"""Entry decomposition: which component reordered our probes.

Inside one entry the order is the order the scheduler handed transactions to
one execution thread; across entries it is the order threads finished. If the
fee rule holds inside entries but weakens across them, the scheduler's pass
was priority-ordered and the parallel threads reordered it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .constants import N_BOOT, N_PERM
from .load import Run
from .orders import OrderSet
from .stats import bb_mask, fifo_pred, pair_rate, priority_pred
from .tidy import clean_entries

SIZE_BUCKETS = ((1, 1, "1"), (2, 4, "2-4"), (5, 16, "5-16"), (17, 64, "17-64"), (65, 10**9, ">64"))


def size_distribution(sizes: np.ndarray) -> dict[str, Any]:
    sizes = np.asarray(sizes, dtype=float)
    if sizes.size == 0:
        return {"n_entries": 0}
    return {
        "n_entries": int(sizes.size),
        "mean": float(sizes.mean()),
        "median": float(np.median(sizes)),
        "p90": float(np.quantile(sizes, 0.9)),
        "max": int(sizes.max()),
        "share_single_tx": float(np.mean(sizes == 1)),
        "histogram": {name: int(np.sum((sizes >= lo) & (sizes <= hi))) for lo, hi, name in SIZE_BUCKETS},
    }


def entries_report(
    os: OrderSet,
    probes: pd.DataFrame,
    runs: list[Run],
    blocks: dict[int, dict[str, Any]],
    blocks_df: pd.DataFrame,
    n_boot: int = N_BOOT,
    n_perm: int = N_PERM,
) -> dict[str, Any]:
    """Same-entry vs cross-entry order rates and entry sizes, per family."""
    if not any(r.entries for r in runs) or probes.empty or not probes["entries_available"].any():
        return {"available": False, "reason": "no entries.jsonl with entries for the landed slots"}
    entry = os.column("entry_index")
    a, b = os.pair_a, os.pair_b
    known = np.isfinite(entry[a]) & np.isfinite(entry[b])
    same = known & (entry[a] == entry[b])
    cross = known & (entry[a] != entry[b])
    out: dict[str, Any] = {"available": True, "families": {}}
    fam_of_slot = dict(zip(blocks_df["slot"].astype(int), blocks_df["family"].astype(str))) if not blocks_df.empty else {}
    all_entries: dict[str, list[int]] = {}
    for run in runs:
        for slot, se in clean_entries(run.entries, blocks).items():
            all_entries.setdefault(fam_of_slot.get(slot, "Unknown"), []).extend(se.counts)
    landed = probes[probes["in_block"] & probes["entry_size"].notna()]
    families = sorted(set(os.bbs["producer_family"].astype(str))) if os.n_bb else []
    for fam in families:
        m = bb_mask(os, producer_family=fam)
        sub = os.subset(m)
        pair_in = np.isin(os.pair_bb, np.flatnonzero(m))
        s_same, s_cross = same[pair_in], cross[pair_in]
        hp, es = priority_pred(sub), fifo_pred(sub)
        kinds = sub.bbs["kind"].astype(str).to_numpy()[sub.pair_bb]
        lf = np.isin(kinds, ["ladder", "format"])
        tie = kinds == "tie"
        out["families"][fam] = {
            "n_pairs_same_entry": int(s_same.sum()),
            "n_pairs_cross_entry": int(s_cross.sum()),
            "share_pairs_same_entry": float(s_same.sum() / max(1, (s_same | s_cross).sum())),
            "higher_priority_first": {
                "same_entry": pair_rate(sub, np.where(s_same & lf, hp, 0), f"entries|{fam}|same|hp", n_boot, n_perm),
                "cross_entry": pair_rate(sub, np.where(s_cross & lf, hp, 0), f"entries|{fam}|cross|hp", n_boot, n_perm),
            },
            "earlier_sent_first_tie": {
                "same_entry": pair_rate(sub, np.where(s_same & tie, es, 0), f"entries|{fam}|same|es", n_boot, n_perm),
                "cross_entry": pair_rate(sub, np.where(s_cross & tie, es, 0), f"entries|{fam}|cross|es", n_boot, n_perm),
            },
            "probe_entry_sizes": size_distribution(landed.loc[landed["producer_family"] == fam, "entry_size"].to_numpy(dtype=float)),
            "block_entry_sizes": size_distribution(np.array(all_entries.get(fam, []), dtype=float)),
        }
    return out
