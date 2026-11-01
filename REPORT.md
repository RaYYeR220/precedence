# Report: how Solana mainnet leaders ordered our transactions, November 2026

Every table in this report is generated from the raw data in `data/` by
`python -m prec_analysis render` and re-checked byte for byte by `docker compose up`. The prose
between the tables is ours.

## 1. When and where every measurement was taken

All runs are after the Alpenglow activation (first Votor slot 459,653,000, 21 October 2026) and
before fast leader handover (feature `FastLeaderHandover111…` not active; every run checks it and
refuses to start if it is). Times are UTC.

<!-- results:measurements:start -->
_No run in the data directory._
<!-- results:measurements:end -->

Leader types at the time of measurement (gossip `clientId`, stake from `getVoteAccounts`):

<!-- results:leader_types:start -->
_No run in the data directory._
<!-- results:leader_types:end -->

## 2. Results by client family

<!-- results:verdicts:start -->
_No results yet: run `python -m prec_analysis analyze` first._
<!-- results:verdicts:end -->

### Per validator

<!-- results:per_validator:start -->
_No results yet: run `python -m prec_analysis analyze` first._
<!-- results:per_validator:end -->

## 3. The predictions, checked

Written from the clients' source code and committed before the first transaction
([docs/PREDICTIONS.md](docs/PREDICTIONS.md)).

<!-- results:predictions:start -->
| Id | Prediction | Outcome | Evidence |
| --- | --- | --- | --- |
| P0 | BAM and Harmonic: the gossip tpuQuic is a proxy, so we measure the external orderer | inconclusive | no window records with tpu_is_proxy |
| P-A1 | Agave/JitoLabs ladder queued before the window: priority order | inconclusive | Agave no data; JitoLabs no data |
| P-A2 | Agave/JitoLabs: the shared arm holds priority order more strictly than distinct | inconclusive | shared − distinct concordance with priority, ladders: Agave no data; JitoLabs no data |
| P-A3 | Agave/JitoLabs tie: oldest first (FIFO) at 2 and 5 ms; no reliable order at 0.5 ms | inconclusive | Agave no data; JitoLabs no data |
| P-A4 | Agave/JitoLabs ladder arriving mid-window: weaker priority order than P-A1 | inconclusive | no data directory |
| P-A5 | Agave/JitoLabs sweep (10–20 ms apart): send order | inconclusive | Agave no data; JitoLabs no data |
| P-A6 | Agave/JitoLabs: inclusion does not depend on fee | inconclusive | fewer than two fee levels with sent ladder probes |
| P-F1 | Firedancer ladder: strict priority order | inconclusive | Firedancer: no data |
| P-F2 | Firedancer tie: newest first (LIFO) when queued together | inconclusive | Firedancer no data |
| P-F3 | Firedancer sweep: send order | inconclusive | Firedancer no data |
| P-F4 | Firedancer: pre-window sends are kept until the blockhash expires | inconclusive | no data directory |
| P-B1 | BAM sweep: batch auction, batched(w) with w between 20 and 50 ms | inconclusive | sweep: AgaveBam no data; FireBAM no data |
| P-B2 | BAM ladder burst: priority order | inconclusive | AgaveBam no data; FireBAM no data |
| P-B3 | BAM: pre-window sends can land in the previous BAM leader's block | inconclusive | no data directory |
| P-B4 | FireBAM orders like AgaveBam | inconclusive | one of the BAM families has no data |
| P-H1 | Harmonic validators on the default fba strategy: batched(w ≈ 50 ms) | inconclusive | no Harmonic validator with data |
| P-H2 | Harmonic validators on fifo: send order regardless of fee | inconclusive | no Harmonic validator with data |
| P-H3 | Harmonic: the validator's own client (Agave vs Firedancer) does not change the order | inconclusive | HarmonicAgave no detected label; HarmonicFiredancer no detected label (queued together / over time) |
| P-R1 | Rakurai ladder: priority order | inconclusive | Rakurai no data |
| P-X1 | Agave and Firedancer: no order difference between v1 and legacy at equal fee | inconclusive | v1 first at equal fee: Agave no data; Firedancer no data |
<!-- results:predictions:end -->

## 4. Scheduler versus parallel execution: entries

<!-- results:entries:start -->
_Entry data not available: no entries.jsonl with entries for the landed slots._
<!-- results:entries:end -->

## 5. Does a later, higher fee still win?

Share of probe pairs where the higher-priority probe came first, by how much later it was sent.

<!-- results:dose_response:start -->
| Higher-priority probe was… | sent earlier or at the same time | < 1 ms later | 1–5 ms later | 5–20 ms later | 20–50 ms later | 50–150 ms later | > 150 ms later |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all families | – | – | – | – | – | – | – |

_Share of pairs where the higher-priority probe came first, by how much later it was sent; 95% interval over batches, n = pairs._
<!-- results:dose_response:end -->

## 6. Equal fees: oldest or newest first

<!-- results:ties:start -->
_No results yet: run `python -m prec_analysis analyze` first._
<!-- results:ties:end -->

## 7. Over time: arrival order, priority, or batches

<!-- results:sweep:start -->
_No results yet: run `python -m prec_analysis analyze` first._
<!-- results:sweep:end -->

## 8. Did the probes land

<!-- results:inclusion:start -->
_No results yet: run `python -m prec_analysis analyze` first._
<!-- results:inclusion:end -->

## 9. Naming the orderer from our probes alone

A classifier that sees only our probes' order, timing and landing names the component that
ordered each block; the same classifier on whole-block statistics (what an observer sees without
probes) is the baseline.

<!-- results:fingerprint:start -->
_No results yet: run `python -m prec_analysis analyze` first._
<!-- results:fingerprint:end -->

## 10. Noise: controls and power

<!-- results:controls:start -->
| Control | Statistic | Expected | Observed |
| --- | --- | --- | ---: |
| Shuffled positions | validators selecting no rule | ≈ 95% | – |
| Shuffled positions | families selecting no rule | ≈ 95% | – |
| Shuffled positions | mean τ, priority vs position (ladder, format) | ≈ 0 | –  |
| Shuffled positions | sign test fires (ladder, format) | ≈ 5% | – |
| Shuffled positions | mean τ, send time vs position (tie, sweep) | ≈ 0 | –  |
| Inert variable (idx parity) | odd idx first | ≈ 50% | –, p = – |
| Inert variable (idx parity) | mean τ | ≈ 0 | – , sign test p = – |
<!-- results:controls:end -->

<!-- results:power:start -->
_No results yet: run `python -m prec_analysis analyze` first._
<!-- results:power:end -->

## 11. Timing of leader windows after Alpenglow

<!-- results:clock:start -->
_No run in the data directory._
<!-- results:clock:end -->

## 12. What went wrong

<!-- results:what_went_wrong_stats:start -->
| What | Count | Detail |
| --- | ---: | --- |
| Input issues logged by the loader | 0 | – |
<!-- results:what_went_wrong_stats:end -->
