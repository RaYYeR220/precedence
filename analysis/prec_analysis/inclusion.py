"""Did our probes land, where, and in whose block.

A probe that never landed has no producer, so landing rates are grouped by
the family of the targeted leader at the time of its window (the one table
in the analysis that is grouped by target). Spill and landing-slot tables
cover landed probes only.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> list[float | None]:
    """Wilson score interval for k successes out of n."""
    if n == 0:
        return [None, None]
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _rate(k: int, n: int) -> dict[str, Any]:
    return {"n": int(n), "k": int(k), "rate": (k / n) if n else None, "ci": wilson(int(k), int(n))}


def _grouped_rates(df: pd.DataFrame, keys: list[str], flag: str) -> list[dict[str, Any]]:
    rows = []
    for vals, grp in df.groupby(keys, sort=True, dropna=False):
        vals = vals if isinstance(vals, tuple) else (vals,)
        row = {k: (None if pd.isna(v) else (int(v) if k == "priority_fee" else str(v))) for k, v in zip(keys, vals)}
        row.update(_rate(int(grp[flag].astype(bool).sum()), len(grp)))
        rows.append(row)
    return rows


def inclusion(probes: pd.DataFrame, windows: pd.DataFrame | None = None) -> dict[str, Any]:
    """Landing, spill and landing-slot tables."""
    if probes.empty:
        return {"n_probes": 0}
    sent = probes[probes["sent"].astype(bool)].copy()
    sent["target_family"] = sent["target_family"].fillna("Unknown")
    landed = sent[sent["landed"].astype(bool)]
    attributed = landed[landed["producer"].notna()]
    out: dict[str, Any] = {
        "n_probes": int(len(probes)),
        "n_sent": int(len(sent)),
        "n_landed": int(len(landed)),
        "n_landed_without_block": int((landed["in_block"] == False).sum()),  # noqa: E712
        "overall": _rate(int(len(landed)), int(len(sent))),
        "by_family": _grouped_rates(sent, ["target_family"], "landed"),
        "by_family_kind": _grouped_rates(sent, ["target_family", "kind"], "landed"),
        "by_family_path": _grouped_rates(sent, ["target_family", "path"], "landed"),
        "by_family_kind_path_fee": _grouped_rates(sent, ["target_family", "kind", "path", "priority_fee"], "landed"),
        "spill_by_family": _grouped_rates(attributed.assign(spilled=attributed["spilled"].astype(bool)), ["target_family"], "spilled"),
    }
    slot_rows = []
    for (fam, kind), grp in landed.groupby(["target_family", "kind"], sort=True):
        counts = grp["slot_in_window"].dropna().astype(int).value_counts().sort_index()
        total = int(counts.sum())
        slot_rows.append(
            {
                "target_family": str(fam),
                "kind": str(kind),
                "n": total,
                "slot_offset_counts": {str(int(k)): int(v) for k, v in counts.items()},
                "share_in_window": float(counts[(counts.index >= 0) & (counts.index <= 3)].sum() / total) if total else None,
            }
        )
    out["landing_slot"] = slot_rows
    changed = attributed[attributed["client_changed"].fillna(False).astype(bool)]
    out["client_changed"] = {
        "n_probes": int(len(changed)),
        "n_windows": int(changed["window_key"].nunique()),
        "validators": sorted(changed["producer"].astype(str).unique().tolist()),
    }
    if windows is not None and not windows.empty:
        out["window_status"] = {str(k): int(v) for k, v in windows["status"].astype(str).value_counts().sort_index().items()}
    return out
