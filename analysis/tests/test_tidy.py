"""Tidy tables: attribution, client labels, pairs and tolerance of missing files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from prec_analysis.load import load_dataset
from prec_analysis.orders import OrderSet
from prec_analysis.tidy import clean_entries, tidy, tidy_dataset

from builders import NEXT, T0, TARGET, build_tree

WS = 463_900_100


def _ladder(slots: list[int | None], leaders: list[str]) -> dict:
    fees = [10_000, 4_000, 1_500, 500]
    return {
        "batch_id": 0,
        "window_first_slot": WS,
        "first_send": T0,
        "probes": [{"idx": i, "fee": fees[i], "send_rank": i, "slot": s, "leader": l} for i, (s, l) in enumerate(zip(slots, leaders))],
    }


def test_spilled_probe_is_attributed_to_its_producer(tmp_path: Path) -> None:
    snaps = [(T0 - 10_000_000, {TARGET: "Agave", NEXT: "Firedancer"})]
    batch = _ladder([WS, WS, WS + 4, None], [TARGET, TARGET, NEXT, TARGET])
    probes, pairs, blocks = tidy_dataset(load_dataset(build_tree(tmp_path, [batch], snaps)))
    spilled = probes[probes["idx"] == 2].iloc[0]
    assert spilled["producer"] == NEXT
    assert bool(spilled["spilled"]) is True
    assert spilled["producer_family"] == "Firedancer"
    assert spilled["target_family"] == "Agave"
    assert int(spilled["slot_in_window"]) == 4
    home = probes[probes["idx"] == 0].iloc[0]
    assert home["producer"] == TARGET and bool(home["spilled"]) is False
    # Only the two probes in the target's block form a pair, and it is the target's.
    assert len(pairs) == 1
    assert pairs.iloc[0]["validator"] == TARGET
    assert set(blocks["leader"]) == {TARGET, NEXT}
    assert not bool(probes[probes["idx"] == 3].iloc[0]["landed"])


def test_client_label_follows_the_nearest_snapshot(tmp_path: Path) -> None:
    t1, t2, t3 = T0 - 600_000_000, T0 + 600_000_000, T0 + 1_800_000_000
    snaps = [(t1, {TARGET: "AgaveBam"}), (t2, {TARGET: "AgaveBam"}), (t3, {TARGET: "JitoLabs"})]
    early = _ladder([WS, WS, None, None], [TARGET] * 4)
    late = _ladder([WS + 1_000, WS + 1_000, None, None], [TARGET] * 4)
    late["batch_id"] = 1
    late["window_first_slot"] = WS + 1_000
    late["first_send"] = t3 - 100_000_000  # between t2 and t3, nearer t3
    probes, _, _ = tidy_dataset(load_dataset(build_tree(tmp_path, [early, late], snaps)))
    e = probes[(probes["batch_id"] == 0) & probes["landed"]].iloc[0]
    lt = probes[(probes["batch_id"] == 1) & probes["landed"]].iloc[0]
    assert e["producer_family"] == "AgaveBam" and bool(e["client_changed"]) is False
    assert lt["producer_family"] == "JitoLabs" and bool(lt["client_changed"]) is True
    assert lt["planned_family"] == "Agave"


def test_batch_with_fewer_than_two_probes_in_a_block_gives_no_pairs(tmp_path: Path) -> None:
    snaps = [(T0, {TARGET: "Agave", NEXT: "Agave"})]
    one_each = _ladder([WS, WS + 1, WS + 2, None], [TARGET, TARGET, TARGET, TARGET])
    probes, pairs, _ = tidy_dataset(load_dataset(build_tree(tmp_path, [one_each], snaps)))
    assert int(probes["landed"].sum()) == 3
    assert pairs.empty
    assert OrderSet.from_probes(probes).n_pairs == 0


def test_pair_outcomes_follow_block_order(tmp_path: Path) -> None:
    snaps = [(T0, {TARGET: "Agave"})]
    # Probes are appended to the block in idx order; fee falls with idx, so the higher fee is always first.
    batch = _ladder([WS, WS, WS, WS], [TARGET] * 4)
    _, pairs, _ = tidy_dataset(load_dataset(build_tree(tmp_path, [batch], snaps)))
    assert len(pairs) == 6
    assert pairs["higher_priority_first"].astype(bool).all()
    assert pairs["earlier_sent_first"].astype(bool).all()
    assert pairs["same_entry"].isna().all()


def test_missing_optional_files_do_not_stop_the_analysis(tmp_path: Path) -> None:
    snaps = [(T0, {TARGET: "Agave"})]
    root = build_tree(tmp_path, [_ladder([WS, WS, WS, None], [TARGET] * 4)], snaps)
    run_dir = next((root / "runs").iterdir())
    (run_dir / "windows.jsonl").unlink()
    (run_dir / "config.json").unlink()
    ds = load_dataset(root)
    probes, pairs, blocks = tidy_dataset(ds)
    assert len(pairs) == 3
    assert not probes["entries_available"].any()
    assert probes["entry_size"].isna().all()


def test_entries_from_a_cleared_bank_are_dropped() -> None:
    block = {"txs": [{}] * 3}
    lines = [
        {"slot": 5, "index": 0, "starting_transaction_index": 0, "executed_transaction_count": 3, "bank_id": 1},
        {"slot": 5, "update_parent": {"cleared_bank_id": 1, "parent_slot": 3}},
        {"slot": 5, "index": 0, "starting_transaction_index": 0, "executed_transaction_count": 1, "bank_id": 2},
        {"slot": 5, "index": 1, "starting_transaction_index": 1, "executed_transaction_count": 0, "bank_id": 2},
        {"slot": 5, "index": 2, "starting_transaction_index": 1, "executed_transaction_count": 2, "bank_id": 2},
    ]
    entries = clean_entries(lines, {5: block})
    assert entries[5].counts == [1, 2]
    assert entries[5].locate(2) == (2, 2, 1)
    assert entries[5].locate(0) == (0, 1, 0)


def test_written_tree_loads_back_to_the_same_tables(small_sim, small_dir: Path) -> None:
    mem = small_sim.to_dataset()
    disk = load_dataset(small_dir)
    p_mem, pairs_mem, _ = tidy(mem.runs, mem.blocks)
    p_disk, pairs_disk, _ = tidy(disk.runs, disk.blocks)
    cols = ["window_key", "idx", "landed_slot", "block_index", "producer", "producer_family", "spilled", "client_changed", "entry_size"]
    pd.testing.assert_frame_equal(p_mem[cols].reset_index(drop=True), p_disk[cols].reset_index(drop=True))
    assert len(pairs_mem) == len(pairs_disk) > 0
    assert p_disk["spilled"].fillna(False).astype(bool).any()
    assert p_disk["client_changed"].fillna(False).astype(bool).any()
    assert p_disk["entries_available"].all() or p_disk.loc[p_disk["in_block"], "entries_available"].all()
