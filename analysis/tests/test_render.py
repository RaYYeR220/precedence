"""The presentation layer: marker blocks, the --check gate, prediction rules, site data."""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest

from prec_analysis.__main__ import main
from prec_analysis.render import BLOCKS, RULES, Context, evaluate, render_blocks
from prec_analysis.render.markers import MARKER, replace_blocks, update_file
from prec_analysis.render.predictions import OUTCOMES, ids_in_document
from prec_analysis.render.site import priority_level

FAST = ["--perms", "199", "--boot", "200", "--power-sims", "50"]
REPO = Path(__file__).resolve().parents[2]


def doc_with_markers(names: list[str], newline: str = "\n") -> str:
    parts = ["# Report", "", "Hand-written intro | with a pipe, kept as is.", ""]
    for name in names:
        parts += [f"## {name}", "", f"<!-- results:{name}:start -->", "old content 12.3%", f"<!-- results:{name}:end -->", "", f"Narrative after {name}.", ""]
    return newline.join(parts)


@pytest.fixture(scope="module")
def results_dir(small_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("results")
    assert main(["analyze", "--data", str(small_dir), "--out", str(out), *FAST]) == 0
    return out


@pytest.fixture()
def rendered(small_dir: Path, results_dir: Path, tmp_path: Path) -> dict[str, Path]:
    readme = tmp_path / "README.md"
    report = tmp_path / "REPORT.md"
    readme.write_bytes(doc_with_markers(["headline", "verdicts", "predictions"]).encode("utf-8"))
    report.write_bytes(doc_with_markers(list(BLOCKS)).encode("utf-8"))
    site = tmp_path / "site"
    args = ["render", "--data", str(small_dir), "--results", str(results_dir), "--readme", str(readme), "--report", str(report), "--site", str(site)]
    assert main(args) == 0
    return {"readme": readme, "report": report, "site": site, "args": args}  # type: ignore[dict-item]


# -- markers -----------------------------------------------------------------------


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_marker_replacement_keeps_surrounding_text_byte_identical(newline: str) -> None:
    text = doc_with_markers(["headline", "verdicts"], newline)
    res = replace_blocks(text, {"headline": "- one\n- two", "verdicts": "| a | b |\n| --- | --- |\n| 1 | 2 |"})
    assert res.changed == ["headline", "verdicts"]
    # Everything outside the markers is unchanged, byte for byte.
    def outside(t: str) -> str:
        return MARKER.sub(lambda m: f"<{m.group('name')}>", t)

    assert outside(res.text) == outside(text)
    assert res.text.startswith(text[: text.index("<!-- results:")])
    assert res.text.endswith(text[text.rindex(":end -->") + len(":end -->") :])
    assert f"<!-- results:headline:start -->{newline}- one{newline}- two{newline}<!-- results:headline:end -->" in res.text
    if newline == "\r\n":
        assert "\n" not in res.text.replace("\r\n", "")
    # A second pass with the same blocks changes nothing.
    again = replace_blocks(res.text, {"headline": "- one\n- two", "verdicts": "| a | b |\n| --- | --- |\n| 1 | 2 |"})
    assert again.text == res.text and again.changed == []


def test_unknown_blocks_and_files_without_markers_are_left_alone(tmp_path: Path) -> None:
    text = doc_with_markers(["no_such_block"])
    res = replace_blocks(text, {"headline": "x"})
    assert res.text == text and res.unknown == ["no_such_block"]
    plain = tmp_path / "plain.md"
    plain.write_bytes(b"# Nothing to fill\r\nhere\n")
    res2, warnings = update_file(plain, {"headline": "x"})
    assert plain.read_bytes() == b"# Nothing to fill\r\nhere\n"
    assert any("no results markers" in w for w in warnings)
    missing = tmp_path / "missing.md"
    res3, warnings = update_file(missing, {"headline": "x"})
    assert res3 is None and not missing.exists() and warnings


# -- render and --check ------------------------------------------------------------


def test_render_fills_every_block_and_leaves_the_narrative(rendered: dict[str, Path]) -> None:
    report = rendered["report"].read_text(encoding="utf-8")
    assert "old content 12.3%" not in report
    for name in BLOCKS:
        assert f"Narrative after {name}." in report
        body = report.split(f"<!-- results:{name}:start -->")[1].split(f"<!-- results:{name}:end -->")[0]
        assert body.strip(), name
    assert "Hand-written intro | with a pipe, kept as is." in report
    verdicts = report.split("<!-- results:verdicts:start -->")[1].split("<!-- results:verdicts:end -->")[0]
    assert verdicts.strip().startswith("| Family |")
    preds = report.split("<!-- results:predictions:start -->")[1].split("<!-- results:predictions:end -->")[0]
    for pid in RULES:
        assert f"| {pid} |" in preds


def test_check_passes_on_fresh_render_and_detects_a_changed_number(rendered: dict[str, Path], results_dir: Path) -> None:
    args = [str(a) for a in rendered["args"]]  # type: ignore[union-attr]
    assert main([*args, "--check"]) == 0
    # A number edited by hand inside a generated block is caught.
    readme = rendered["readme"]
    text = readme.read_text(encoding="utf-8")
    block = text.split("<!-- results:headline:start -->")[1].split("<!-- results:headline:end -->")[0]
    digit = next(c for c in block if c.isdigit())
    edited = block.replace(digit, "9" if digit != "9" else "8", 1)
    readme.write_text(text.replace(block, edited), encoding="utf-8", newline="\n")
    assert main([*args, "--check"]) == 1
    # Re-rendering repairs it; then a changed result is caught too.
    assert main(args) == 0
    assert main([*args, "--check"]) == 0
    summary = results_dir / "summary.json"
    original = summary.read_text(encoding="utf-8")
    try:
        data = json.loads(original)
        data["inputs"]["n_probes_landed"] += 1
        summary.write_text(json.dumps(data), encoding="utf-8")
        assert main([*args, "--check"]) == 1
    finally:
        summary.write_text(original, encoding="utf-8", newline="\n")


def test_check_detects_a_changed_site_file(rendered: dict[str, Path]) -> None:
    args = [str(a) for a in rendered["args"]]  # type: ignore[union-attr]
    path = rendered["site"] / "data" / "overview.json"
    path.write_bytes(path.read_bytes().replace(b'"n_landed":', b'"n_landed": ', 1))
    assert main([*args, "--check"]) == 1


def test_blocks_render_without_any_results() -> None:
    blocks = render_blocks(Context(results={}, data=None))
    assert set(blocks) == set(BLOCKS)
    for name, text in blocks.items():
        assert text.strip(), name


# -- predictions ---------------------------------------------------------------------


def test_every_prediction_id_has_a_rule() -> None:
    ids = ids_in_document(REPO / "docs" / "PREDICTIONS.md")
    assert ids, "no prediction ids found"
    assert list(RULES) == ids
    for pid, rule in RULES.items():
        doc = rule.fn.__doc__ or ""
        assert ("Reads" in doc or "Needs" in doc), f"{pid}: docstring must name the results field it reads"


def test_missing_results_make_every_prediction_inconclusive() -> None:
    outcomes = evaluate(Context(results={}, data=None))
    assert len(outcomes) == len(RULES)
    assert {o.outcome for o in outcomes} == {"inconclusive"}


def _rate(rate: float, lo: float, hi: float, n_batches: int = 10) -> dict:
    return {"rate": rate, "ci": [lo, hi], "n_batches": n_batches, "n_pairs": 80, "p_perm": 0.01}


def _family(**card: dict) -> dict:
    return {"rule_card": {"simultaneous": {}, "ties": {}, "over_time": {}, "conflicts": {}, "format": {}, **card}}


def test_predictions_read_the_rule_card() -> None:
    results = {
        "summary": {
            "families": {
                "Firedancer": _family(ties={"rule": "lifo", "earlier_sent_first": _rate(0.1, 0.02, 0.2)}),
                "Agave": _family(
                    over_time={"rule": "fifo", "earlier_sent_first": _rate(0.9, 0.8, 0.97)},
                    ties={"rule": "fifo", "earlier_sent_first": _rate(0.9, 0.8, 0.95), "by_spacing": {"0.5ms": {"all": _rate(0.55, 0.4, 0.7)}, "2ms": {"all": _rate(0.9, 0.8, 1.0)}}},
                ),
                "JitoLabs": _family(
                    over_time={"rule": "priority", "higher_priority_first": _rate(0.7, 0.6, 0.8)},
                    ties={"rule": "fifo", "earlier_sent_first": _rate(0.9, 0.8, 0.95), "by_spacing": {"0.5ms": {"all": _rate(0.95, 0.9, 1.0)}}},
                ),
                "Rakurai": _family(simultaneous={"rule": "random", "p_perm": 0.4}),
                "AgaveBam": _family(over_time={"rule": "batched", "batched": {"w_ms": 30.0, "w_range_ms": [30.0, 40.0], "w_identifiable": True, "reads_as": "batch auction"}}),
                "FireBAM": _family(over_time={"rule": "batched", "batched": {"w_ms": 100.0, "w_range_ms": [100.0, 150.0], "w_identifiable": True, "reads_as": "batch auction"}}),
            }
        },
    }
    by_id = {o.id: o for o in evaluate(Context(results=results, data=None))}
    assert by_id["P-F2"].outcome == "confirmed"
    assert by_id["P-A5"].outcome == "refuted"  # JitoLabs orders sweeps by fee
    assert by_id["P-R1"].outcome == "inconclusive"  # no rule beats random
    assert by_id["P-A3"].outcome == "refuted"  # JitoLabs orders 0.5 ms ties reliably
    assert "0.5 ms is reliably ordered too" in by_id["P-A3"].evidence
    assert by_id["P-B1"].outcome == "refuted"  # FireBAM batches are far longer than 50 ms
    assert all(o.outcome in OUTCOMES for o in by_id.values())
    # Agave alone satisfies P-A3: FIFO at 2 ms and no reliable order at 0.5 ms.
    agave_only = {"summary": {"families": {"Agave": results["summary"]["families"]["Agave"]}}}
    assert {o.id: o for o in evaluate(Context(results=agave_only, data=None))}["P-A3"].outcome == "confirmed"


# -- site --------------------------------------------------------------------------------


def _load_block(data_dir: Path, slot: int) -> dict:
    with gzip.open(data_dir / "blocks" / f"{slot}.json.gz", "rb") as fh:
        return json.loads(fh.read())


def test_site_data_has_one_strip_per_batch_and_probes_at_their_ledger_index(rendered: dict[str, Path], small_dir: Path) -> None:
    site = rendered["site"]
    batches = json.loads((site / "data" / "batches.json").read_text(encoding="utf-8"))
    js = (site / "data" / "batches.js").read_text(encoding="utf-8")
    assert js.startswith("window.PRECEDENCE_BATCHES=") and js.rstrip().endswith(";")
    assert json.loads(js[len("window.PRECEDENCE_BATCHES=") : js.rstrip().rindex(";")]) == batches
    planned = []
    for run_dir in sorted((small_dir / "runs").iterdir()):
        design = json.loads((run_dir / "design.json").read_text(encoding="utf-8"))
        planned += [(design["run_id"], b["batch_id"]) for b in design["batches"]]
    got = [(b["run"], b["batch"]) for b in batches["batches"]]
    assert sorted(got) == sorted(planned) and len(got) == len(set(got))
    n_strips = 0
    for b in batches["batches"]:
        assert b["n_landed"] == sum(len(e["probes"]) for e in b["blocks"])
        for entry in b["blocks"]:
            n_strips += 1
            blk = batches["blocks"][str(entry["slot"])]
            assert len(blk["levels"]) == blk["n_tx"]
            if blk["entries"] is not None:
                assert blk["entries_start"] + sum(blk["entries"]) == blk["n_tx"]
            raw = _load_block(small_dir, entry["slot"])
            sig_at = {int(t["i"]): t["sig"] for t in raw["txs"]}
            assert raw["leader"] == entry["producer"]
            for p in entry["probes"]:
                assert 0 <= p["i"] < blk["n_tx"]
                assert sig_at[p["i"]] == p["sig"]
                assert 1 <= p["fee_rank"] <= b["n_ranks"] and 1 <= p["send_rank"] <= b["n_planned"]
    assert n_strips > 0
    overview = json.loads((site / "data" / "overview.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in overview["predictions"]] == list(RULES)
    for page in ("index.html", "blocks.html"):
        html = (site / page).read_text(encoding="utf-8")
        for ref in ("assets/app.css", "assets/common.js", "data/"):
            assert ref in html
        for src in [s.split("?")[0] for s in html.split('src="')[1:]]:
            assert (site / src).exists(), src


def test_site_is_deterministic(rendered: dict[str, Path], tmp_path: Path) -> None:
    args = [str(a) for a in rendered["args"]]  # type: ignore[union-attr]
    other = tmp_path / "again"
    i = args.index("--site")
    assert main(args[: i + 1] + [str(other)] + args[i + 2 :]) == 0
    for path in sorted(rendered["site"].rglob("*")):
        if path.is_file():
            assert (other / path.relative_to(rendered["site"])).read_bytes() == path.read_bytes(), path.name


def test_stake_share_comes_from_the_snapshot(small_dir: Path, results_dir: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(small_dir, data)
    run_dir = next((data / "runs").iterdir())
    nodes = json.loads(next((run_dir / "snapshot").glob("nodes-*.json")).read_text(encoding="utf-8"))
    votes = {"current": [{"nodePubkey": n["pubkey"], "activatedStake": 1_000 * (k + 1)} for k, n in enumerate(nodes)], "delinquent": []}
    (run_dir / "snapshot" / "vote_accounts.json").write_text(json.dumps(votes), encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(doc_with_markers(["verdicts", "leader_types"]), encoding="utf-8")
    assert main(["render", "--data", str(data), "--results", str(results_dir), "--readme", str(readme)]) == 0
    text = readme.read_text(encoding="utf-8")
    verdicts = text.split("<!-- results:verdicts:start -->")[1].split("<!-- results:verdicts:end -->")[0]
    rows = [line for line in verdicts.splitlines() if line.startswith("| ") and "Family" not in line and "---" not in line]
    assert rows and all("%" in line.split("|")[2] for line in rows)
    leaders = text.split("<!-- results:leader_types:start -->")[1].split("<!-- results:leader_types:end -->")[0]
    first_table = leaders.strip().split(2 * chr(10))[0]
    lrows = [line.split("|") for line in first_table.splitlines() if line.startswith("| ") and "Client family" not in line and "---" not in line]
    shares = [float(r[2].strip().rstrip("%")) for r in lrows]
    assert lrows and abs(sum(shares) - 100.0) < 0.5
    assert sum(int(r[3].strip()) for r in lrows) == len(nodes)


def test_clock_block_reads_first_shred_times(small_dir: Path, results_dir: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(small_dir, data)
    run_dir = next((data / "runs").iterdir())
    design = json.loads((run_dir / "design.json").read_text(encoding="utf-8"))
    windows = [json.loads(line) for line in (run_dir / "windows.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    first_send = {w["batch_id"]: w["first_send_unix_us"] for w in windows}
    # Exact window starts placed 50 ms after each batch's first send; slots inside the window 150 ms apart.
    rows = []
    for b in design["batches"]:
        ws = b["window_first_slot"] - b["window_first_slot"] % 4
        t0 = first_send[b["batch_id"]] + 50_000
        for k in range(5):
            rows.append({"slot": ws + k, "recv_unix_us": t0 + k * 150_000 + 900, "server_unix_us": t0 + k * 150_000, "source": "ws", "status": "firstShredReceived"})
        b["window_first_slot"] = ws
    (run_dir / "slots.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (run_dir / "design.json").write_text(json.dumps(design), encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(doc_with_markers(["clock"]), encoding="utf-8")
    assert main(["render", "--data", str(data), "--results", str(results_dir), "--readme", str(readme)]) == 0
    block = readme.read_text(encoding="utf-8").split("<!-- results:clock:start -->")[1].split("<!-- results:clock:end -->")[0]
    assert "150 (n=" in block and "300 (n=" in block and "450 (n=" in block
    assert "| 600 / 600 / " in block  # p10 and median of the window period; the tail mixes batches' timelines
    assert "-50 / -50 / -50" in block


def test_data_splits_for_predictions(small_dir: Path) -> None:
    from prec_analysis.render.context import load_data_facts

    facts = load_data_facts(small_dir)
    assert facts is not None
    families = tuple(sorted(set(facts.probes["producer_family"].dropna().astype(str))))
    pre = facts.ladder_priority_by_anchor(families, "before_window")
    assert pre is not None and pre["n_pairs"] > 0 and 0.0 <= pre["rate"] <= 1.0
    by_anchor = facts.landing_by_anchor(str(facts.probes["target_family"].iloc[0]))
    assert set(by_anchor) <= {"before_window", "in_window"} and all(v["n"] >= v["k"] for v in by_anchor.values())
    early = facts.early_landings(families)
    assert early["n_landed"] >= early["n_early"] >= sum(early["producers"].values()) - 0


LOCALNET = REPO / "localnet"


@pytest.mark.skipif(not (LOCALNET / "results" / "summary.json").exists(), reason="no localnet positive-control results")
def test_localnet_positive_control_renders_as_one_family(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_bytes(doc_with_markers(list(BLOCKS)).encode("utf-8"))
    site = tmp_path / "site"
    args = ["render", "--data", str(LOCALNET / "data"), "--results", str(LOCALNET / "results"), "--readme", str(readme), "--site", str(site)]
    assert main(args) == 0
    assert main([*args, "--check"]) == 0
    text = readme.read_text(encoding="utf-8")
    summary = json.loads((LOCALNET / "results" / "summary.json").read_text(encoding="utf-8"))
    sentence = summary["families"]["Agave"]["rule_card"]["sentence"]
    verdicts = text.split("<!-- results:verdicts:start -->")[1].split("<!-- results:verdicts:end -->")[0]
    assert sentence in verdicts
    overview = json.loads((site / "data" / "overview.json").read_text(encoding="utf-8"))
    assert [f["name"] for f in overview["families"]] == ["Agave"]
    assert overview["dose"]["order"] == ["Agave"]
    assert [c["part"] for c in overview["families"][0]["card"]] == ["simultaneous", "ties", "over_time"]
    batches = json.loads((site / "data" / "batches.json").read_text(encoding="utf-8"))
    design = json.loads(next((LOCALNET / "data" / "runs").iterdir()).joinpath("design.json").read_text(encoding="utf-8"))
    assert len(batches["batches"]) == len(design["batches"])
    assert {b["mode"] for b in batches["batches"]} == {"localnet"}
    assert batches["families"] == ["Agave"]


def test_priority_levels_are_one_character() -> None:
    assert priority_level(float("nan")) == "-"
    assert priority_level(0.0) == "-"
    assert priority_level(1e3) == "0"
    assert priority_level(1e6) == "9"
    assert priority_level(1e40) == "z"
