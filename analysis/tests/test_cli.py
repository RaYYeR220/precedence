"""The command line: deterministic results and the --check gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from prec_analysis.__main__ import main
from prec_analysis.report import OUTPUT_FILES

FAST = ["--perms", "199", "--boot", "200", "--power-sims", "50"]


def test_analyze_is_byte_for_byte_deterministic(small_dir: Path, tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["analyze", "--data", str(small_dir), "--out", str(a), *FAST]) == 0
    assert main(["analyze", "--data", str(small_dir), "--out", str(b), *FAST]) == 0
    for name in OUTPUT_FILES:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    summary = json.loads((a / "summary.json").read_text(encoding="utf-8"))
    assert summary["inputs"]["n_pairs"] > 0
    assert summary["validators"]


def test_check_passes_on_fresh_results_and_fails_on_changed_ones(small_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "results"
    assert main(["analyze", "--data", str(small_dir), "--out", str(out), *FAST]) == 0
    assert main(["analyze", "--data", str(small_dir), "--out", str(out), "--check", *FAST]) == 0
    path = out / "models.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"verdict"', '"verdict_"', 1), encoding="utf-8")
    assert main(["analyze", "--data", str(small_dir), "--out", str(out), "--check", *FAST]) == 1


def test_module_entry_point_simulates_a_tree(tmp_path: Path) -> None:
    out = tmp_path / "sim"
    done = subprocess.run(
        [sys.executable, "-m", "prec_analysis", "simulate", "--out", str(out), "--seed", "2", "--preset", "small"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert done.returncode == 0, done.stderr
    runs = list((out / "runs").iterdir())
    assert len(runs) == 1
    for name in ("design.json", "windows.jsonl", "probes.jsonl", "statuses.json", "entries.jsonl", "footers.jsonl", "slots.jsonl", "config.json"):
        assert (runs[0] / name).exists(), name
    assert list((runs[0] / "snapshot").glob("nodes-*.json"))
    assert list((out / "blocks").glob("*.json.gz"))
    assert (out / "ledger" / "spend.jsonl").exists()
