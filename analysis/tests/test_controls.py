"""Negative controls must find nothing."""

from __future__ import annotations

from prec_analysis.controls import inert_control, shuffled_control
from prec_analysis.models import sign_matrix
from prec_analysis.orders import OrderSet
from prec_analysis.tidy import probes_frame


def test_shuffled_positions_give_no_tau_and_select_random(small_sim) -> None:
    ds = small_sim.to_dataset()
    os = OrderSet.from_probes(probes_frame(ds.runs, ds.blocks))
    out = shuffled_control(os, sign_matrix(os), n_perm=499, n_boot=200)
    assert abs(out["tau_priority_ladder_format"]["mean_tau"]) < 0.03
    assert abs(out["tau_send_tie_sweep"]["mean_tau"]) < 0.03
    assert out["tau_priority_ladder_format"]["sign_test_rejection_rate"] <= 0.12
    assert out["share_validators_random"] >= 0.8
    assert out["share_families_random"] >= 0.8


def test_inert_variable_shows_nothing(small_sim) -> None:
    ds = small_sim.to_dataset()
    os = OrderSet.from_probes(probes_frame(ds.runs, ds.blocks))
    out = inert_control(os, n_boot=500, n_perm=999)
    rate = out["odd_idx_first"]
    assert rate["n_pairs"] > 100
    assert abs(rate["rate"] - 0.5) < 0.08
    assert rate["p_perm"] > 0.01
    assert abs(out["tau_parity"]["mean"]) < 0.15
