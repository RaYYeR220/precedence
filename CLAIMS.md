# Claims and their evidence

Every statement this repository makes, with how strongly it is supported and what reproduces it.

| Tier | Meaning |
|---|---|
| **VERIFIED-LIVE** | checked against mainnet by `precedence verify` on every run of `docker compose up` |
| **REPRODUCIBLE** | recomputed from the committed raw data, byte for byte, by `docker compose up` |
| **MODELED** | the output of a statistical model on reproducible data; its assumptions are stated |
| **NOT CLAIMED** | something we are often assumed to say, and do not |

## Measurement facts

| Claim | Tier | Evidence |
|---|---|---|
| Each probe landed at the recorded slot and position | VERIFIED-LIVE | `precedence verify` (`getBlock`, signature index) |
| The block's producer is the recorded leader | VERIFIED-LIVE | `precedence verify` (`Fee` reward pubkey) |
| Our stored copy of each block has the chain's exact transaction order | VERIFIED-LIVE | `precedence verify` (full signature list) |
| Recorded entry boundaries are the block's real entries | VERIFIED-LIVE | `precedence verify` recomputes the entry hash chain from signatures and requires it to end on the blockhash |
| All measurements are post-Alpenglow and pre fast-leader-handover | REPRODUCIBLE | `data/runs/*/snapshot/features.json`; every slot ≥ 459,653,000 |
| The client of each producer at measurement time | REPRODUCIBLE | `data/runs/*/snapshot/nodes-*.json` (gossip `clientId`, version), nearest snapshot to each batch |
| BAM and Harmonic validators advertise a TPU on another host | REPRODUCIBLE | `data/runs/*/snapshot/nodes-*.json` (`tpuQuic` IP vs `gossip` IP); `leader_types` table |
| Send times and the design were fixed before sending | REPRODUCIBLE | `design.json` (hash logged before the first send), `probes.jsonl` (µs stream-open times) |
| Spend stayed under the cap; reserved and settled lamports | REPRODUCIBLE | `data/ledger/spend.jsonl`; balances via `precedence status` |
| Post-Alpenglow window timing (in-window slot offsets, window period) | REPRODUCIBLE | `data/runs/*/slots.jsonl`; REPORT §11 |

## Results

| Claim | Tier | Evidence |
|---|---|---|
| Per validator and family: the rule for probes queued together, for ties, and over time (over time: batches that reached the leader inside its window, by measured send phase) | MODELED | `results/summary.json` → `rule_card`; Mallows models, permutation tests (`analysis/prec_analysis/card.py`, `models.py`) |
| A rule's strictness (concordance, θ) and its interval | MODELED | bootstrap over batches; `results/summary.json` |
| "No rule detectable" for a part | MODELED | permutation test fails at α = 0.05; the minimum detectable effect is in `results/power.json` |
| Batch length w of a batch auction | MODELED | grid search over w and phase, with an identifiability check (`results/models.json`) |
| Scheduler decision vs parallel execution | MODELED on VERIFIED-LIVE inputs | same-entry vs cross-entry pairs (`results/entries.json`); entries verified as above |
| Prediction outcomes | REPRODUCIBLE | `docs/PREDICTIONS.md` (pre-registered) → REPORT §3, rules in `analysis/prec_analysis/render/predictions.py` |
| How well our probes, and whole-block statistics, name the orderer (per validator about equally well, per window the probes better; both above chance) | MODELED | leave-one-validator-out classification, `results/fingerprint.json` and `results/baseline.json` |
| The pipeline recovers a known rule | REPRODUCIBLE | `localnet/` (real Agave 4.3.2) and `python -m prec_analysis validate` (simulation) |

## Not claimed

- **The source code of BAM nodes or Harmonic builders.** For external orderers we describe the rule
  their output follows; we did not read their code, which is not public.
- **Results for staked senders.** All probes were sent unstaked.
- **That a validator's rule is fixed.** Operators change configuration, and external builders can
  change strategy; each result holds for its dated measurement.
- **Anything about Jito bundles or tips.** None were sent; they are out of scope.
- **That mainnet entries are single transactions by design.** We report the entry sizes we
  observed; why a producer records them that way is outside what probes can show.
