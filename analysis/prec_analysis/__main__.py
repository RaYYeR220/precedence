"""Command line: analyze, simulate, validate, render.

    python -m prec_analysis analyze  --data data --out results [--check]
    python -m prec_analysis simulate --out data-sim --seed 1 --preset validation
    python -m prec_analysis validate --out results [--seed 7] [--reps 6]
    python -m prec_analysis render   --data data --results results --readme README.md --report REPORT.md --site site/dist [--check]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .constants import N_BOOT, N_PERM, POWER_SIMS
from .render import add_parser as add_render_parser
from .report import Settings, dumps, run_analysis
from .simulate import PRESETS, simulate
from .validate import run_validation


def _analyze(args: argparse.Namespace) -> int:
    settings = Settings(n_perm=args.perms, n_boot=args.boot, power_sims=args.power_sims)
    start = time.perf_counter()
    bad = run_analysis(args.data, args.out, check=args.check, settings=settings)
    took = time.perf_counter() - start
    if args.check:
        if bad:
            print(f"results differ from {args.out}: {', '.join(bad)}", file=sys.stderr)
            return 1
        print(f"results in {args.out} reproduce exactly ({took:.1f} s)")
        return 0
    print(f"wrote results to {args.out} ({took:.1f} s)")
    return 0


def _simulate(args: argparse.Namespace) -> int:
    data = simulate(args.out, args.seed, args.preset)
    n_probes = sum(len(r.probes) for r in data.runs)
    print(f"wrote {len(data.runs)} run(s), {n_probes} probes, {len(data.blocks)} blocks to {args.out}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    start = time.perf_counter()
    result = run_validation(seed=args.seed, reps=args.reps, n_perm=args.perms, n_boot=args.boot)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "simulation_validation.json").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps(result))
    for name, h in result["headline"].items():
        print(f"{name}: accuracy {h['accuracy']:.3f} over {h['n_validators']} validators; identifiable {h['accuracy_identifiable']:.3f} over {h['n_identifiable']}")
    print(f"wrote {out / 'simulation_validation.json'} ({time.perf_counter() - start:.1f} s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prec_analysis", description="Analysis of precedence runs")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="analyse a data directory into results JSON")
    a.add_argument("--data", type=Path, default=Path("data"))
    a.add_argument("--out", type=Path, default=Path("results"))
    a.add_argument("--check", action="store_true", help="recompute and fail if the results differ from --out")
    a.add_argument("--perms", type=int, default=N_PERM)
    a.add_argument("--boot", type=int, default=N_BOOT)
    a.add_argument("--power-sims", type=int, default=POWER_SIMS)
    a.set_defaults(func=_analyze)

    s = sub.add_parser("simulate", help="write a synthetic data directory with known rules")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--preset", choices=sorted(PRESETS), default="validation")
    s.set_defaults(func=_simulate)

    v = sub.add_parser("validate", help="simulation-recovery experiment → simulation_validation.json")
    v.add_argument("--out", type=Path, default=Path("results"))
    v.add_argument("--seed", type=int, default=7)
    v.add_argument("--reps", type=int, default=6, help="synthetic validators per rule")
    v.add_argument("--perms", type=int, default=N_PERM)
    v.add_argument("--boot", type=int, default=200)
    v.set_defaults(func=_validate)

    add_render_parser(sub)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
