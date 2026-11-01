"""Hand-built data trees for tests: a few probes, blocks and snapshots, written to disk."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from prec_analysis.constants import agave_priority

RUN_ID = "20261020T1200Z-abc123"
T0 = 1_792_000_000_000_000
TARGET = "Target1111111111111111111111111111111111111"
NEXT = "NextLeader11111111111111111111111111111111"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def node(pubkey: str, client_id: str) -> dict[str, Any]:
    return {"pubkey": pubkey, "clientId": client_id, "version": "4.3.2", "gossip": "1.1.1.1:8001", "tpuQuic": "1.1.1.1:8009"}


def probe_tx(sig: str, fee: int, i: int) -> dict[str, Any]:
    return {"i": i, "sig": sig, "version": "1", "fee": 5_000 + fee, "cost_units": 1_400, "cu_consumed": 300, "priority_fee": fee,
            "cu_limit": 8_000, "n_sigs": 1, "payer": f"payer-{sig}", "writable": [f"payer-{sig}"], "err": False}


def foreign_tx(i: int, fee: int) -> dict[str, Any]:
    return {"i": i, "sig": f"foreign-{i}-{fee}", "version": "legacy", "fee": 5_000 + fee, "cost_units": 30_000, "cu_consumed": 20_000,
            "priority_fee": fee, "cu_limit": 200_000, "n_sigs": 1, "payer": f"fp-{i}", "writable": [f"fp-{i}"], "err": False}


def build_tree(
    root: Path,
    batches: list[dict[str, Any]],
    snapshots: list[tuple[int, dict[str, str]]],
    with_entries: bool = False,
    entries: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a minimal but complete data tree.

    Each batch: {"batch_id", "kind", "arm", "window_first_slot", "first_send", "probes": [
        {"idx", "fee", "send_rank", "slot" | None, "leader" (of that slot)}]}.
    Block contents: foreign transactions, then the batch's probes in the order listed.
    """
    run = root / "runs" / RUN_ID
    design_batches, windows, probes, statuses = [], [], [], {}
    blocks: dict[int, dict[str, Any]] = {}
    for b in batches:
        bid = b["batch_id"]
        dprobes = []
        for p in b["probes"]:
            sig = f"sig-{bid}-{p['idx']}"
            dprobes.append({"idx": p["idx"], "priority_fee": p["fee"], "format": "v1", "send_rank": p["send_rank"], "send_offset_us": 0, "payer_slot": p["idx"]})
            probes.append({
                "batch_id": bid, "idx": p["idx"], "signature": sig, "payer": f"payer-{sig}", "shared_write": None,
                "priority_fee": p["fee"], "format": "v1", "est_cost": 9_087, "agave_priority": agave_priority(p["fee"], 1, 9_087),
                "send_rank": p["send_rank"], "planned_offset_us": 0, "sent_unix_us": b["first_send"] + 40 * p["send_rank"],
                "sent_done_unix_us": b["first_send"] + 40 * p["send_rank"] + 100, "send_err": None,
            })
            statuses[sig] = {"slot": p["slot"], "err": None} if p.get("slot") is not None else None
            if p.get("slot") is not None:
                blk = blocks.setdefault(p["slot"], {"slot": p["slot"], "blockhash": "bh", "previous_blockhash": "pbh", "parent_slot": p["slot"] - 1,
                                                    "block_time": T0 // 1_000_000, "block_height": 1, "leader": p["leader"], "raw_sha256": "0" * 64,
                                                    "txs": [foreign_tx(0, 0), foreign_tx(1, 1_000)]})
                blk["txs"].append(probe_tx(sig, p["fee"], len(blk["txs"])))
        design_batches.append({
            "batch_id": bid, "target": TARGET, "family": "Agave", "version": "4.3.2", "tpu_quic": "1.1.1.1:8009", "tpu_is_proxy": False,
            "window_first_slot": b["window_first_slot"], "path": "quic",
            "design": {"kind": b.get("kind", "ladder"), "arm": b.get("arm", "distinct"), "anchor": {"type": "before_window", "slots": 1.0},
                       "spacing_us": 0, "seed": bid, "probes": dprobes},
            "design_hash": "h",
        })
        windows.append({
            "batch_id": bid, "target": TARGET, "family": "Agave", "version": "4.3.2", "window_first_slot": b["window_first_slot"],
            "tpu_quic": "1.1.1.1:8009", "tpu_is_proxy": False, "path": "quic", "kind": b.get("kind", "ladder"), "arm": b.get("arm", "distinct"),
            "slot_us": 213_000.0, "predicted_window_start_unix_us": b["first_send"] + 213_000, "first_send_unix_us": b["first_send"],
            "rtt_us": 1_000, "handshake_us": 2_000, "blockhash": "bh", "blockhash_slot": 1, "status": "sent",
        })
    _write_json(run / "config.json", {"run_id": RUN_ID, "mode": "live", "run_seed": 1})
    _write_json(run / "design.json", {"run_id": RUN_ID, "run_seed": 1, "sha256": "s", "batches": design_batches})
    _write_jsonl(run / "windows.jsonl", windows)
    _write_jsonl(run / "probes.jsonl", probes)
    _write_json(run / "statuses.json", statuses)
    for ts, clients in snapshots:
        _write_json(run / "snapshot" / f"nodes-{ts}.json", [node(pk, cid) for pk, cid in clients.items()])
    if with_entries:
        _write_jsonl(run / "entries.jsonl", entries or [])
    for slot, blk in blocks.items():
        path = root / "blocks" / f"{slot}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wb") as fh:
            fh.write(json.dumps(blk).encode())
    return root
