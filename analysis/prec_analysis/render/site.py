"""The static explorer: data files and pages under `site/dist/`.

Two data files feed two pages. `data/overview.json` holds what the overview
draws (verdicts, dose-response, ties, inclusion, predictions); it comes from
the results only. `data/batches.json` holds one record per planned batch with
the blocks its probes landed in: every transaction of each block in ledger
order, reduced to a priority level, plus entry sizes and our probes. Each
JSON file has a `.js` twin that assigns the same object to a global, so the
pages also work when opened straight from the file system.

Every file is a pure function of the inputs: sorted keys, no timestamps, and
asset URLs carry a content hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd

from ..constants import GAP_BUCKETS_MS
from ..report import to_jsonable
from ..tidy import SlotEntries, clean_entries, tx_priority
from .blocks import DOSE_LABELS, family_order, headline_items
from .context import Context, DataFacts
from .fmt import ORDERER_CLASS_TEXT, is_num, model_text, rule_text
from .predictions import evaluate, outcome_counts

STATIC_FILES = ("app.css", "common.js", "overview.js", "blocks.js")
PAGES = ("index.html", "blocks.html")

# Priority level of a transaction: 3 steps per decade from 10^3, one base-36 digit.
LEVEL_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"
LEVEL_FLOOR_LOG10 = 3.0
LEVELS_PER_DECADE = 3


def templates_dir(explicit: Path | str | None = None) -> Path:
    """Where the page templates and static assets live."""
    candidates = [explicit, os.environ.get("PREC_TEMPLATES"), Path(__file__).resolve().parents[2] / "templates"]
    for c in candidates:
        if c and (Path(c) / "site").is_dir():
            return Path(c)
    raise FileNotFoundError("templates not found: pass --templates or set PREC_TEMPLATES to analysis/templates")


def dumps_compact(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _strip_md(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


# -- overview ---------------------------------------------------------------------


def _unit_row(name: str, u: dict[str, Any], stake: float | None) -> dict[str, Any]:
    return {
        "name": name,
        "family": u.get("family"),
        "orderer_class": u.get("orderer_class"),
        "orderer_text": ORDERER_CLASS_TEXT.get(u.get("orderer_class") or "", None),
        "stake_share": stake,
        "n_validators": u.get("n_validators"),
        "n_windows": u.get("n_windows"),
        "n_landed": u.get("n_landed_probes"),
        "n_pairs": u.get("n_pairs"),
        "verdict": u.get("verdict"),
        "best_rule": u.get("best_rule"),
        "rule_text": rule_text(u.get("best_rule"), u.get("w_ms")) if u.get("verdict") == "rule" else None,
        "reference_text": model_text(u.get("reference_model")),
        "w_ms": u.get("w_ms"),
        "theta": u.get("theta"),
        "theta_ci": u.get("theta_ci"),
        "concordance": u.get("concordance"),
        "concordance_ci": u.get("concordance_ci"),
        "p_perm": u.get("p_perm"),
        "component": u.get("component"),
        "validator_rules": {rule_text(k): v for k, v in (u.get("validator_rules") or {}).items()},
        "mde": (u.get("min_detectable") or {}).get("concordance_mde"),
        "tie": (u.get("key_rates") or {}).get("earlier_sent_first_tie"),
        "ladder": (u.get("key_rates") or {}).get("higher_priority_first_ladder_format"),
    }


def _stats(ctx: Context) -> list[dict[str, str]]:
    s = ctx.summary
    if not s:
        return []
    inp = s.get("inputs", {})
    inc = ctx.r("inclusion").get("overall", {})
    fams = [f for f in ctx.families().values() if f.get("verdict") != "insufficient_data"]
    vals = [v for v in ctx.validators().values() if v.get("verdict") != "insufficient_data"]
    out = [
        {"value": f"{int(inp.get('n_probes_landed') or 0):,}", "label": f"probes landed of {int(inp.get('n_probes_sent') or 0):,} sent ({_pct(inc.get('rate'))})"},
        {"value": f"{int(inp.get('n_windows_sent') or 0):,}", "label": f"leader windows probed in {len(inp.get('runs', []))} run(s), {int(inp.get('n_blocks') or 0):,} blocks"},
    ]
    if fams:
        out.append(
            {
                "value": f"{sum(f.get('verdict') == 'rule' for f in fams)} of {len(fams)}",
                "label": f"families with a detectable rule; {sum(v.get('verdict') == 'rule' for v in vals)} of {len(vals)} validators",
            }
        )
    fp = s.get("fingerprint", {}).get("validator_level", {})
    if is_num(fp.get("probe_accuracy")):
        out.append({"value": _pct(fp["probe_accuracy"]), "label": f"of validators' orderers named from our probes alone; whole-block baseline {_pct(fp.get('baseline_accuracy'))}, chance {_pct(fp.get('chance_majority'))}"})
    c = outcome_counts(evaluate(ctx))
    out.append({"value": f"{c['confirmed']} of {sum(c.values())}", "label": f"predictions confirmed; {c['refuted']} refuted, {c['inconclusive']} inconclusive"})
    return out


def _pct(x: Any) -> str:
    return f"{float(x) * 100:.1f}%" if is_num(x) else "–"


def overview_data(ctx: Context) -> dict[str, Any]:
    s = ctx.summary
    fams = ctx.families()
    vals = ctx.validators()
    pbc = ctx.r("pairs_by_condition")
    inc = ctx.r("inclusion")
    fam_names = family_order(list(fams))
    stake_f = (lambda f: ctx.data.stake_share_of_family(f)) if ctx.data else (lambda f: None)
    stake_v = (lambda v: ctx.data.stake_share_of(v)) if ctx.data and ctx.data.stake else (lambda v: None)
    validators = [_unit_row(v, u, stake_v(v)) for v, u in sorted(vals.items(), key=lambda kv: (-(kv[1].get("n_landed_probes") or 0), kv[0]))]
    buckets = ["hp_sent_earlier_or_same"] + [name for _, _, name in GAP_BUCKETS_MS]
    dose = {}
    for unit, rows in (pbc.get("dose_response_by_family") or {}).items():
        by = {r.get("bucket"): r for r in rows}
        dose[unit] = [{"bucket": b, "rate": by.get(b, {}).get("rate"), "ci": by.get(b, {}).get("ci"), "n_pairs": by.get(b, {}).get("n_pairs"), "n_batches": by.get(b, {}).get("n_batches")} for b in buckets]
    spill = {r["target_family"]: r for r in inc.get("spill_by_family", [])}
    inclusion = [
        {"family": r["target_family"], "n": r.get("n"), "rate": r.get("rate"), "ci": r.get("ci"), "spill": (spill.get(r["target_family"]) or {}).get("rate"), "spill_ci": (spill.get(r["target_family"]) or {}).get("ci")}
        for r in sorted(inc.get("by_family", []), key=lambda r: family_order([x["target_family"] for x in inc.get("by_family", [])]).index(r["target_family"]))
    ]
    fp = (s.get("fingerprint") or {})
    return {
        "meta": {
            "analysis_version": (s.get("inputs") or {}).get("analysis_version"),
            "runs": (s.get("inputs") or {}).get("runs", []),
            "settings": (s.get("inputs") or {}).get("settings", {}),
            "n_pairs": (s.get("inputs") or {}).get("n_pairs"),
        },
        "headline": [_strip_md(x) for x in headline_items(ctx)],
        "stats": _stats(ctx),
        "families": [{**_unit_row(f, fams[f], stake_f(f)), "validators": [v["name"] for v in validators if v["family"] == f]} for f in fam_names],
        "validators": validators,
        "dose": {"buckets": buckets, "labels": {b: DOSE_LABELS[b] for b in buckets}, "units": dose, "order": ["all"] + [f for f in fam_names if f in dose]},
        "inclusion": {"overall": inc.get("overall"), "families": inclusion},
        "fingerprint": fp,
        "predictions": [{"id": o.id, "prediction": o.prediction, "outcome": o.outcome, "evidence": o.evidence} for o in evaluate(ctx)],
    }


# -- batches and blocks -----------------------------------------------------------


def priority_level(p: float) -> str:
    """One base-36 digit: 3 levels per decade of Agave priority above 10^3; '-' if unknown."""
    if not (isinstance(p, float) and math.isfinite(p)) or p <= 0:
        return "-"
    lvl = int(round((math.log10(p) - LEVEL_FLOOR_LOG10) * LEVELS_PER_DECADE))
    return LEVEL_BASE36[min(max(lvl, 0), len(LEVEL_BASE36) - 1)]


def _block_record(block: dict[str, Any], entries: SlotEntries | None) -> dict[str, Any]:
    txs = block.get("txs", [])
    ordered = sorted(txs, key=lambda t: int(t.get("i", 0)))
    sizes = None
    if entries is not None and entries.counts:
        sizes = [int(c) for c in entries.counts]
    return {
        "slot": int(block["slot"]),
        "producer": block.get("leader"),
        "n_tx": len(ordered),
        "levels": "".join(priority_level(tx_priority(t)) for t in ordered),
        "entries": sizes,
        "entries_start": int(entries.starts[0]) if sizes else None,
        "block_time": block.get("block_time"),
    }


def _dense_rank_desc(values: list[int]) -> dict[int, int]:
    """1 = highest value, equal values share a rank."""
    return {v: i + 1 for i, v in enumerate(sorted(set(values), reverse=True))}


def _pair_counts(rows: pd.DataFrame) -> dict[str, list[int]]:
    """Pairs of landed probes in one block: higher priority first, earlier sent first."""
    pos = rows["block_index"].astype(int).to_numpy()
    prio = rows["agave_priority"].astype(float).to_numpy()
    t = rows["t_send_us"].astype(float).to_numpy()
    hp = [0, 0]
    es = [0, 0]
    for a in range(len(pos)):
        for b in range(a + 1, len(pos)):
            first, second = (a, b) if pos[a] < pos[b] else (b, a)
            if prio[a] != prio[b]:
                hp[1] += 1
                hp[0] += int(prio[first] > prio[second])
            if t[a] != t[b]:
                es[1] += 1
                es[0] += int(t[first] < t[second])
    return {"higher_priority_first": hp, "earlier_sent_first": es}


def batches_data(facts: DataFacts) -> dict[str, Any]:
    ds = facts.dataset
    probes = facts.probes
    entries: dict[int, SlotEntries] = {}
    for run in ds.runs:
        entries.update(clean_entries(run.entries, ds.blocks))
    blocks_out: dict[str, Any] = {}
    batches: list[dict[str, Any]] = []
    families: set[str] = set()
    validators: dict[str, str] = {}
    by_batch = {key: grp for key, grp in probes.groupby(["run_id", "batch_id"], sort=False)} if not probes.empty else {}
    for run in ds.runs:
        windows = {int(w["batch_id"]): w for w in run.windows if "batch_id" in w}
        for b in sorted(run.batches, key=lambda x: int(x["batch_id"])):
            bid = int(b["batch_id"])
            design = b.get("design", {}) or {}
            w = windows.get(bid, {})
            dprobes = design.get("probes", []) or []
            fee_rank = _dense_rank_desc([int(q.get("priority_fee", 0)) for q in dprobes])
            n_planned = len(dprobes)
            rows = by_batch.get((run.run_id, bid))
            rec: dict[str, Any] = {
                "run": run.run_id,
                "batch": bid,
                "kind": design.get("kind"),
                "arm": design.get("arm"),
                "path": b.get("path"),
                "anchor": design.get("anchor"),
                "spacing_us": design.get("spacing_us"),
                "target": b.get("target"),
                "target_family": b.get("family"),
                "window_first_slot": b.get("window_first_slot"),
                "status": w.get("status", "missing"),
                "tpu_is_proxy": w.get("tpu_is_proxy", b.get("tpu_is_proxy")),
                "n_planned": n_planned,
                "n_ranks": len(fee_rank),
                "n_sent": 0,
                "n_landed": 0,
                "blocks": [],
                "unlanded": [],
            }
            if b.get("family"):
                families.add(str(b["family"]))
            if rows is not None and len(rows):
                first_send = rows["t_send_us"].min()
                rec["n_sent"] = int(rows["sent"].sum())
                rec["n_landed"] = int(rows["in_block"].sum())
                for slot, grp in rows[rows["in_block"]].groupby("landed_slot", sort=True):
                    slot = int(slot)
                    if str(slot) not in blocks_out and slot in ds.blocks:
                        blocks_out[str(slot)] = _block_record(ds.blocks[slot], entries.get(slot))
                    grp = grp.sort_values("block_index")
                    producer = grp["producer"].iloc[0]
                    pfam = str(grp["producer_family"].iloc[0]) if pd.notna(grp["producer_family"].iloc[0]) else "Unknown"
                    families.add(pfam)
                    if producer is not None:
                        validators.setdefault(str(producer), pfam)
                    rec["blocks"].append(
                        {
                            "slot": slot,
                            "producer": producer,
                            "family": pfam,
                            "spilled": bool(grp["spilled"].fillna(False).astype(bool).any()),
                            "slot_in_window": None if pd.isna(grp["slot_in_window"].iloc[0]) else int(grp["slot_in_window"].iloc[0]),
                            "probes": [
                                {
                                    "i": int(r.block_index),
                                    "idx": int(r.idx),
                                    "fee": int(r.priority_fee),
                                    "fee_rank": fee_rank.get(int(r.priority_fee)),
                                    "send_rank": int(r.send_rank) + 1,
                                    "dt_us": int(r.t_send_us - first_send),
                                    "format": r.format,
                                    "sig": r.signature,
                                    "entry": None if pd.isna(r.entry_index) else int(r.entry_index),
                                }
                                for r in grp.itertuples()
                            ],
                            "pairs": _pair_counts(grp),
                        }
                    )
                for r in rows[~rows["in_block"]].sort_values("send_rank").itertuples():
                    why = "send error" if isinstance(r.send_err, str) and r.send_err else ("not sent" if not r.sent else ("landed, block missing" if r.landed else "did not land"))
                    rec["unlanded"].append({"idx": int(r.idx), "fee": int(r.priority_fee), "fee_rank": fee_rank.get(int(r.priority_fee)), "send_rank": int(r.send_rank) + 1, "why": why})
            batches.append(rec)
    return {
        "level_encoding": {"alphabet": LEVEL_BASE36, "floor_log10": LEVEL_FLOOR_LOG10, "per_decade": LEVELS_PER_DECADE, "unknown": "-"},
        "families": family_order(sorted(families)),
        "validators": [{"id": v, "family": f} for v, f in sorted(validators.items(), key=lambda kv: (kv[1], kv[0]))],
        "blocks": blocks_out,
        "batches": batches,
    }


# -- files --------------------------------------------------------------------------


def _js_twin(var: str, text: str) -> str:
    return f"window.{var}={text};\n"


def site_files(ctx: Context, templates: Path | None = None) -> dict[str, bytes]:
    """Relative path under site/dist → exact bytes of every generated file."""
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    tdir = templates_dir(templates) / "site"
    files: dict[str, bytes] = {}
    ov = overview_data(ctx)
    overview = dumps_compact(ov)
    files["data/overview.json"] = (overview + "\n").encode("utf-8")
    files["data/overview.js"] = _js_twin("PRECEDENCE_OVERVIEW", overview).encode("utf-8")
    batches = dumps_compact(batches_data(ctx.data)) if ctx.data is not None else dumps_compact({"batches": [], "blocks": {}, "families": [], "validators": []})
    files["data/batches.json"] = (batches + "\n").encode("utf-8")
    files["data/batches.js"] = _js_twin("PRECEDENCE_BATCHES", batches).encode("utf-8")
    for name in STATIC_FILES:
        files[f"assets/{name}"] = (tdir / "static" / name).read_bytes().replace(b"\r\n", b"\n")
    version = {path: hashlib.sha256(data).hexdigest()[:10] for path, data in files.items()}
    env = Environment(loader=FileSystemLoader(str(tdir)), autoescape=True, undefined=StrictUndefined, keep_trailing_newline=True)
    meta = ov["meta"]
    for page in PAGES:
        html = env.get_template(f"{page}.j2").render(v=version, meta=meta, runs=meta.get("runs", []))
        files[page] = html.replace("\r\n", "\n").encode("utf-8")
    files[".nojekyll"] = b""
    return files


def write_site(files: dict[str, bytes], out_dir: Path) -> None:
    for rel, data in files.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)


def check_site(files: dict[str, bytes], out_dir: Path) -> list[str]:
    return [rel for rel, data in files.items() if not (out_dir / rel).exists() or (out_dir / rel).read_bytes() != data]


__all__ = ["site_files", "write_site", "check_site", "overview_data", "batches_data", "templates_dir", "priority_level"]
