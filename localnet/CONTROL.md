# Positive control: a local Agave 4.3.2 validator

Before reading a rule off mainnet blocks, we checked that the whole pipeline (probe design,
QUIC sender, collector, entry boundaries, verifier and analysis) recovers the rule of a block
producer whose code we can read. A local `solana-test-validator` 4.3.2 is that producer: it is
the leader of every slot and orders transactions with Agave's greedy scheduler.

Reproduce with `docker compose --profile localnet run --rm localnet` (about four minutes, no
keys, no network). The raw data is in [`data/`](data), the analysis output in
[`results/`](results). Entry boundaries come from the validator's own ledger
(`agave-ledger-tool slot -vv`), so here they are ground truth.

Two differences from mainnet, both deliberate: the test validator deactivates the Alpenglow
feature, so its blocks still contain ticks and votes (consensus differs, the scheduler does not:
Alpenglow left execution untouched); and it is the leader of every slot, so there are no leader
windows to aim at and every batch arrives while the scheduler is running.

## What the pipeline recovered

<!-- results:verdicts:start -->
| Family | Stake | Validators | Windows | Landed probes | Queued together | Ties | Over time | Best single rule | Strictness: concordance [CI], θ | Component | Verdict |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |
| Agave (local scheduler) | – | 1 | 30 | 264 | priority: 100.0% in one entry, 55.0% across entries | oldest first: 95.8% [89.9, 99.4] | send order: 100.0% [100.0, 100.0] | arrival order (FIFO) (p = 0.001); validators: 1 arrival order (FIFO) | 90.3% [84.0, 95.5], θ 1.11 [0.76, 1.70] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 55% across entries), arrival order across scheduling passes (sweep 100% send order), ties oldest first (96%). |
<!-- results:verdicts:end -->

## Scheduler decision versus parallel execution

The decisive check: pairs of our probes recorded **in the same entry** were scheduled in one
batch on one thread; pairs in **different entries** were scheduled separately and recorded in
the order the worker threads finished.

<!-- results:entries:start -->
| Family | Pairs in one entry | Pairs across entries | Priority first, same entry | Priority first, across | Tie FIFO, same entry | Tie FIFO, across | Mean entry size | Single-tx entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Agave | 76 | 981 | 100.0% [100.0, 100.0] | 55.0% [44.2, 67.0] | 100.0% [100.0, 100.0] | 95.8% [89.4, 99.4] | 1.1 | 94.3% |

_Inside one entry the order is the scheduler's hand-off to one thread; across entries it is the order threads finished._
<!-- results:entries:end -->

## Ties, sweeps and conflicts

<!-- results:ties:start -->
| Family | Tie pairs | Batches | Earlier sent first | p | Direction | 0.5 ms apart | 2 ms apart | 5 ms apart | Distinct arm | Shared arm |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| Agave | 336 | 12 | 95.8% [89.6, 99.4] | 0.001 | oldest first (FIFO) | 98.4% [97.2, 99.6] b=9 | 100.0% [100.0, 100.0] b=1 | 82.1% [64.3, 100.0] b=2 | 91.7% [80.4, 98.2] | 100.0% [100.0, 100.0] |

_Earlier sent first: share of equal-fee pairs where the earlier-sent probe came first; by spacing from the rule card, b = batches._
<!-- results:ties:end -->

<!-- results:sweep:start -->
| Validator | Family | Over-time rule (rule card) | Best batch length w (ΔAIC ≤ 2 range) | w identifiable | Sweep: higher priority first | Sweep: earlier sent first | Batched check p | Best single rule, all batches |
| --- | --- | --- | ---: | --- | ---: | ---: | --- | --- |
| [`AaSy…vQtQ`](https://solscan.io/account/AaSynsynQffFUg2nyH7ZVpqWkVb3watAqqYSGx1ovQtQ) | Agave | send order: 100.0% [100.0, 100.0] | 5 ms (5 ms–10 ms) | no | 0.0% [0.0, 0.0] n=385 | 100.0% [100.0, 100.0] | – | arrival order (FIFO) |

_Sweeps raise the fee with send time, so fee order and send order disagree on every pair; a batch length is identifiable when it orders at least 2% of pairs differently from both._
<!-- results:sweep:end -->

## Predictions checked against this control

<!-- results:predictions:start -->
| Id | Prediction | Outcome | Evidence |
| --- | --- | --- | --- |
| P0 | BAM and Harmonic: the gossip tpuQuic is a proxy, so we measure the external orderer | inconclusive | no BAM or Harmonic window |
| P-A1 | Agave/JitoLabs ladder queued before the window: priority order | confirmed | Agave priority (100.0% in one entry, 55.0% across entries); JitoLabs no data |
| P-A2 | Agave/JitoLabs: the shared arm holds priority order more strictly than distinct | confirmed | shared − distinct concordance with priority, ladders: Agave +21.4 pts [+1.2, +40.5]; JitoLabs no data |
| P-A3 | Agave/JitoLabs tie: oldest first (FIFO) at 2 and 5 ms; no reliable order at 0.5 ms | refuted | Agave oldest first (95.8% [89.9, 99.4]); earlier sent first by spacing: 0.5ms 98.4% [97.2, 99.6] (9 batches), 2ms 100.0% [100.0, 100.0] (1 batches), 5ms 82.1% [64.3, 100.0] (2 batches); 0.5 ms is reliably ordered too; JitoLabs no data |
| P-A4 | Agave/JitoLabs ladder arriving mid-window: weaker priority order than P-A1 | inconclusive | higher priority first, ladder: before the window –; in the window 64.9% [53.6, 77.1] n=336 (needs 3+ batches each) |
| P-A5 | Agave/JitoLabs sweep (10–20 ms apart): send order | confirmed | Agave send order (100.0% [100.0, 100.0]); JitoLabs no data |
| P-A6 | Agave/JitoLabs: inclusion does not depend on fee | confirmed | landed 100.0% at fee 0 vs 100.0% at 10,000 lamports; χ² p = 1.000 over 8 fee levels |
| P-F1 | Firedancer ladder: strict priority order | inconclusive | Firedancer: no data |
| P-F2 | Firedancer tie: newest first (LIFO) when queued together | inconclusive | Firedancer no data |
| P-F3 | Firedancer sweep: send order | inconclusive | Firedancer no data |
| P-F4 | Firedancer: pre-window sends are kept until the blockhash expires | inconclusive | Firedancer: 0 probes sent before the window and 0 in it |
| P-B1 | BAM sweep: batch auction, batched(w) with w between 20 and 50 ms | inconclusive | sweep: AgaveBam no data; FireBAM no data |
| P-B2 | BAM ladder burst: priority order | inconclusive | AgaveBam no data; FireBAM no data |
| P-B3 | BAM: pre-window sends can land in the previous BAM leader's block | inconclusive | 0 of 0 landed BAM-targeted probes landed before the window; their producers: none |
| P-B4 | FireBAM orders like AgaveBam | inconclusive | one of the BAM families has no data |
| P-H1 | Harmonic validators on the default fba strategy: batched(w ≈ 50 ms) | inconclusive | no Harmonic validator with data |
| P-H2 | Harmonic validators on fifo: send order regardless of fee | inconclusive | no Harmonic validator with data |
| P-H3 | Harmonic: the validator's own client (Agave vs Firedancer) does not change the order | inconclusive | HarmonicAgave no detected label; HarmonicFiredancer no detected label (queued together / over time) |
| P-R1 | Rakurai ladder: priority order | inconclusive | Rakurai no data |
| P-X1 | Agave and Firedancer: no order difference between v1 and legacy at equal fee | inconclusive | v1 first at equal fee: Agave no data; Firedancer no data |
<!-- results:predictions:end -->

## Verification

`precedence verify` against the restarted local validator re-reads every probe block and
recomputes each block's entry hash chain from its transaction signatures: see
[`results/verify.json`](results/verify.json).

<!-- results:controls:start -->
| Control | Statistic | Expected | Observed |
| --- | --- | --- | ---: |
| Shuffled positions | validators selecting no rule | ≈ 95% | 100.0% |
| Shuffled positions | families selecting no rule | ≈ 95% | 100.0% |
| Shuffled positions | mean τ, priority vs position (ladder, format) | ≈ 0 | -0.002 [-0.167, 0.149] |
| Shuffled positions | sign test fires (ladder, format) | ≈ 5% | 2.0% |
| Shuffled positions | mean τ, send time vs position (tie, sweep) | ≈ 0 | 0.004 [-0.106, 0.134] |
| Inert variable (idx parity) | odd idx first | ≈ 50% | 52.9% [44.5, 61.5] n=384, p = 0.544 |
| Inert variable (idx parity) | mean τ | ≈ 0 | 0.043 [-0.087, 0.173], sign test p = 0.664 |

| Family | Blocks | Spearman priority vs index | Adjacent pairs falling in priority | Zero-priority share | Mean tx per block |
| --- | ---: | ---: | ---: | ---: | ---: |
| Agave | 31 | -0.278 [-0.451, -0.092] | 67.5% | 18.9% | 10 |

_What an observer of whole blocks sees without our probes (supplementary; negative Spearman = higher priority earlier)._
<!-- results:controls:end -->
