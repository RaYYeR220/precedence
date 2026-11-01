"""Rule cards: the per-condition answer, checked on a real local Agave validator and on simulated data."""

from __future__ import annotations

from pathlib import Path

import pytest

from prec_analysis.load import load_dataset
from prec_analysis.report import Settings, analyze

LOCALNET = Path(__file__).resolve().parents[2] / "localnet" / "data"


@pytest.fixture(scope="module")
def localnet_summary() -> dict:
    if not (LOCALNET / "runs").is_dir():
        pytest.skip("localnet positive-control data not present")
    out = analyze(load_dataset(LOCALNET), Settings(n_perm=999, n_boot=500, power_sims=50))
    return out["summary.json"]


def _units(summary: dict) -> list[dict]:
    return list(summary["validators"].values()) + list(summary["families"].values())


def test_localnet_card_reads_priority_within_entries(localnet_summary: dict) -> None:
    for unit in _units(localnet_summary):
        sim = unit["rule_card"]["simultaneous"]
        assert sim["scope"] == "same_entry"
        assert sim["rule"] == "priority" and sim["detectable"]
        same = sim["entries"]["same_entry"]
        assert same["n_pairs"] > 0 and same["rate"] == 1.0
        assert sim["entries"]["cross_entry"]["rate"] < same["rate"]


def test_localnet_card_reads_arrival_order_over_time_and_for_ties(localnet_summary: dict) -> None:
    for unit in _units(localnet_summary):
        card = unit["rule_card"]
        assert card["over_time"]["rule"] == "fifo"
        assert card["over_time"]["earlier_sent_first"]["rate"] == 1.0
        assert card["ties"]["rule"] == "fifo"
        assert card["ties"]["earlier_sent_first"]["rate"] > 0.9


def test_headline_text_is_the_card_sentence(localnet_summary: dict) -> None:
    for unit in _units(localnet_summary):
        card = unit["rule_card"]
        assert unit["verdict_text"] == card["sentence"]
        assert unit["single_model_text"] and unit["single_model_text"] != card["sentence"]
        assert card["sentence"].startswith("Priority among probes queued together (100% within an entry")
        assert "arrival order across scheduling passes" in card["sentence"]
        # Every earlier summary key is still there.
        for key in ("verdict", "best_model", "concordance", "concordance_ci", "component", "key_rates", "min_detectable"):
            assert key in unit


def test_card_components_recover_each_conditions_behaviour() -> None:
    from prec_analysis.validate import run_validation

    out = run_validation(seed=11, reps=3, n_perm=999, n_boot=100, designs={"shipped": "rising"})
    per_kind = out["designs"]["shipped"]["per_kind"]
    for name in ("simultaneous", "ties", "over_time"):
        assert per_kind[name]["n"] >= 15
        assert per_kind[name]["accuracy"] >= 0.9, (name, per_kind[name])
