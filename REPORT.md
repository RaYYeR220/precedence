# Report: how Solana mainnet leaders ordered our transactions, November 2026

Every table in this report is generated from the raw data in `data/` by
`python -m prec_analysis render` and re-checked byte for byte by `docker compose up`. The prose
between the tables is ours.

## 1. When and where every measurement was taken

All runs are after the Alpenglow activation (first Votor slot 459,653,000, 21 October 2026) and
before fast leader handover (feature `FastLeaderHandover111…` not active; every run checks it and
refuses to start if it is). Times are UTC.

<!-- results:measurements:start -->
| Run | Mode | Start (UTC) | End (UTC) | Slots covered | Batches sent | Skipped by reason | Probes sent | Landed | Reserved (lamports) | Settled (lamports) |
| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: |
| `20261101T1747Z-868587` | live | 2026-11-01 17:47 | 2026-11-01 17:58 | 464,181,512–464,183,755 | 35 of 50 | 15 late | 278 | 259 | 3,024,630 | 1,715,650 |
| `20261101T1808Z-b6227f` | live | 2026-11-01 18:08 | 2026-11-01 19:09 | 464,187,308–464,196,051 | 183 of 256 | 16 error, 57 late_at_send | 1,224 | 716 | 14,406,760 | 4,879,970 |
| `20261101T1925Z-383540` | live | 2026-11-01 19:25 | 2026-11-01 20:26 | 464,208,824–464,217,479 | 255 of 255 | 0 | 2,232 | 1,054 | 15,356,880 | 7,259,575 |
| `20261101T2038Z-2c72e1` | live | 2026-11-01 20:38 | 2026-11-01 21:41 | 464,229,440–464,238,367 | 251 of 251 | 0 | 2,192 | 896 | 15,050,260 | 6,156,780 |
| `20261101T2151Z-6cdb94` | live | 2026-11-01 21:51 | – | 464,249,708–464,258,655 | 216 of 225 | 9 connect | 1,882 | 1,105 | 12,907,790 | 7,586,260 |
| `20261101T2345Z-e8bc54` | live | 2026-11-01 23:45 | 2026-11-02 01:33 | 464,281,712–464,290,599 | 196 of 197 | 1 connect | 1,732 | 1,716 | 11,849,410 | 11,737,010 |
| `20261102T1908Z-bce300` | live | 2026-11-02 19:08 | 2026-11-02 20:00 | 464,607,244–464,616,043 | 117 of 117 | 0 | 1,036 | 1,036 | 7,122,150 | 7,122,150 |
| all runs | – | – | – | – | 1,253 of 1,351 | 10 connect, 16 error, 15 late, 57 late_at_send | 10,576 | 6,782 | 79,717,880 | 46,457,395 |
<!-- results:measurements:end -->

The first run was a pilot (one batch of each kind per target). Runs 2–5 carried two harness
faults that we found from their own data and fixed before run 6 (section 12): connections that
the server had pruned, and batches signed minutes before their window. Probes that landed in
those runs are valid evidence of order and are kept; batches that could not land are kept out of
the landing statistics. Run 6 is the clean run: 1,716 of 1,732 probes landed. Every run is after
the Alpenglow activation and before fast leader handover; each run checked both feature gates
before sending (`snapshot/features.json`).


Leader types at the time of measurement (gossip `clientId`, stake from `getVoteAccounts`):

<!-- results:leader_types:start -->
| Client family (gossip clientId) | Stake | Staked validators | Gossip nodes | tpuQuic host differs from gossip host | Most common version |
| --- | ---: | ---: | ---: | ---: | --- |
| AgaveBam | 35.8% | 352 | 354 | 99.7% of 354 | 4.4.0-rc.1 (40.6%) |
| JitoLabs | 24.0% | 148 | 962 | 2.0% of 952 | 4.4.0 (38.5%) |
| HarmonicAgave | 21.0% | 46 | 87 | 52.9% of 87 | 4.3.2 (60.9%) |
| HarmonicFiredancer | 9.0% | 43 | 50 | 86.0% of 50 | 26.10.5 (65.1%) |
| Firedancer | 5.4% | 30 | 128 | 4.7% of 128 | 26.10.5 (60.0%) |
| FireBAM | 1.9% | 20 | 20 | 100.0% of 20 | 26.10.5 (100.0%) |
| Rakurai | 1.2% | 9 | 17 | 0.0% of 16 | 4.3.2 (77.8%) |
| Agave | 1.0% | 18 | 2,278 | 0.1% of 1,724 | 4.3.2 (44.4%) |
| Raiku | 0.1% | 1 | 4 | 0.0% of 4 | 4.4.0 (100.0%) |
| Unknown(11951) | 0.1% | 1 | 1 | 0.0% of 1 | 4.4.0 (100.0%) |
| Unknown(16) | 0.0% | 1 | 1 | 0.0% of 1 | 0.1.0 (100.0%) |
| Unknown(42) | 0.0% | 1 | 1 | – | 4.2.1 (100.0%) |
| Frankendancer | 0.0% | 0 | 3 | 0.0% of 2 | 0.101.40300 (66.7%) |
| Unknown(19785) | 0.0% | 0 | 3 | – | 0.0.0 (100.0%) |
| Unknown(61453) | 0.0% | 0 | 3 | – | 0.1.0 (100.0%) |
| Unknown(28263) | 0.0% | 0 | 2 | – | 0.0.0 (100.0%) |

_Snapshot nearest the first run's start (2026-11-01 17:47 UTC); stake from getVoteAccounts; versions among staked nodes when stake is known._

| Producer's gossip clientId | Client declared in the block footer | Probe blocks |
| --- | --- | ---: |
| JitoLabs | JitoLabs | 187 |
| HarmonicAgave | HarmonicAgave | 152 |
| AgaveBam | JitoLabs | 142 |
| HarmonicFiredancer | (empty) | 109 |
| Firedancer | (empty) | 81 |
| Agave | Agave | 39 |
| FireBAM | (empty) | 36 |
| Rakurai | Rakurai | 35 |
| Unknown | JitoLabs | 11 |

_Footers are self-reported by the producer (SIMD-0307 user agent) and were recorded only when the entry stream delivered them._
<!-- results:leader_types:end -->

Two things in this table decide how to read everything that follows. First, for every BAM and
Harmonic validator the address gossip advertises for the TPU is on another host than the
validator's gossip address, and the same host fronts many validators: it is a BAM node or a
Harmonic remote TPU. Our transactions therefore never reach those validators' own schedulers.
Second, the block footer that Alpenglow added (a self-declared user agent) does not tell BAM
apart: BAM-connected validators declare themselves `JitoLabs`, and Firedancer-based clients leave
the field empty. Only gossip `clientId` identifies the BAM connection, so client labels in this
study come from gossip snapshots taken at the start of each run and every 10 minutes during it.


## 2. Results by client family

<!-- results:verdicts:start -->
| Family | Stake | Validators | Windows | Landed probes | Queued together | Ties | Over time | Best single rule | Strictness: concordance [CI], θ | Component | Verdict |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |
| AgaveBam (BAM node) | 35.8% | 23 | 124 | 953 | priority: 92.0% [89.7, 94.0] | newest first: 60.4% [49.2, 71.1] | batch auction, w = 20 ms: w within 10 ms–20 ms | priority, ties newest first (p = 0.001); validators: 18 none detectable, 3 batch auction, 2 priority, ties newest first | 74.8% [67.9, 81.6], θ 0.47 [0.31, 0.70] | probes queued together are sorted by priority; arrivals are grouped into ~20 ms batches (a batch auction) | Priority among probes queued together (92% of pairs), batch auction of about 20 ms (batches in arrival order, priority inside), ties newest first (60%); v1 ahead at equal fee (69%). |
| JitoLabs (local scheduler) | 24.0% | 15 | 173 | 1,411 | priority: 100.0% in one entry, 58.3% across entries | oldest first: 67.1% [60.0, 73.8] | send order: 95.5% [88.9, 100.0] | batch auction, w = 10 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 5 ms; validators: 11 none detectable, 4 batch auction | 73.4% [68.4, 78.2], θ 0.44 [0.32, 0.58] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 58% across entries), arrival order across scheduling passes (sweep 96% send order), ties oldest first (67%); non-conflicting probes are interleaved by parallel execution (61% in rule order vs 88% when they conflict). |
| HarmonicAgave (Harmonic builder) | 21.0% | 19 | 154 | 1,229 | priority: 69.9% [65.8, 74.0] | none detectable: p = 0.529, detectable from 61.2% | send order: 88.0% [84.3, 90.8] | batch auction, w = 10 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 20 ms; validators: 11 none detectable, 4 batch auction, 3 arrival order (FIFO), 1 priority, ties oldest first | 65.1% [60.5, 69.2], θ 0.26 [0.17, 0.34] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (70% of pairs), arrival order across scheduling passes (sweep 88% send order), no rule detectable for ties (p=0.53; 80% power needs concordance >= 0.61); non-conflicting probes are interleaved by parallel execution (57% in rule order vs 77% when they conflict). |
| HarmonicFiredancer (Harmonic builder) | 9.0% | 11 | 121 | 1,037 | priority: 89.7% [85.3, 93.3] | none detectable: p = 0.964, detectable from 61.3% | batch auction, w = 20 ms | batch auction, w = 10 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 5 ms, priority, ties oldest first; validators: 5 priority, ties oldest first, 3 priority, ties newest first, 2 none detectable, 1 batch auction | 67.4% [59.5, 75.3], θ 0.30 [0.15, 0.49] | probes queued together are sorted by priority; arrivals are grouped into ~20 ms batches (a batch auction) | Priority among probes queued together (90% of pairs), batch auction of about 20 ms (batches in arrival order, priority inside), no rule detectable for ties (p=0.96; 80% power needs concordance >= 0.61); v1 ahead at equal fee (98%). |
| Firedancer (Firedancer pack) | 5.4% | 4 | 111 | 939 | priority: 95.1% [91.3, 98.2] | newest first: 89.0% [85.1, 92.9] | scheduler passes, w = 5 ms: w not identifiable | priority, ties newest first (p = 0.001); validators: 3 priority, ties newest first, 1 none detectable | 71.7% [62.4, 81.1], θ 0.38 [0.19, 0.65] | probes queued together are sorted by priority | Priority among probes queued together (95% of pairs), priority within passes of about 5 ms, passes in arrival order, ties newest first (89%); v1 ahead at equal fee (100%). |
| FireBAM (BAM node) | 1.9% | 4 | 41 | 354 | priority: 94.2% [91.5, 97.0] | newest first: 76.8% [62.0, 89.7] | batch auction, w = 20 ms | priority, ties newest first (p = 0.001); validators: 3 priority, ties newest first, 1 batch auction | 69.1% [57.0, 81.8], θ 0.32 [0.10, 0.67] | probes queued together are sorted by priority; arrivals are grouped into ~20 ms batches (a batch auction) | Priority among probes queued together (94% of pairs), batch auction of about 20 ms (batches in arrival order, priority inside), ties newest first (77%); v1 ahead at equal fee (81%). |
| Rakurai (local scheduler) | 1.2% | 3 | 51 | 451 | priority: 100.0% in one entry, 69.0% across entries | none detectable: p = 0.048, detectable from 66.4% | send order: 93.6% [85.3, 98.8] | batch auction, w = 10 ms (p = 0.001); validators: 2 batch auction, 1 priority, ties newest first | 71.2% [62.7, 79.3], θ 0.36 [0.20, 0.57] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 69% across entries), arrival order across scheduling passes (sweep 94% send order), no rule detectable for ties (p=0.05; 80% power needs concordance >= 0.66); non-conflicting probes are interleaved by parallel execution (64% in rule order vs 84% when they conflict); v1 ahead at equal fee (83%). |
| Agave (local scheduler) | 1.0% | 3 | 48 | 377 | priority: 100.0% in one entry, 78.2% across entries | none detectable: p = 0.342, detectable from 75.0% | send order: 89.8% [79.8, 99.2] | priority, ties oldest first (p = 0.001); validators: 1 batch auction, 1 priority, ties oldest first, 1 none detectable | 56.7% [45.3, 68.3], θ 0.11 [0.00, 0.33] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 78% across entries), arrival order across scheduling passes (sweep 90% send order), no rule detectable for ties (p=0.34; 80% power needs concordance >= 0.75). |
| Unknown (–) | 0.0% | 6 | 9 | 31 | send order: 90.0% [84.2, 100.0] | no data | no data | arrival order (FIFO) (p = 0.001); validators: 6 none detectable | 90.0% [83.8, 100.0], θ 1.87 [1.17, ≥ 12] | arrival order: no priority pass reached our probes | Send order among probes queued together (90% of pairs). |
<!-- results:verdicts:end -->

**Local schedulers (Agave, JitoLabs, Rakurai).** The same rule as the open-source scheduler, and
the same rule the local Agave control showed (`localnet/CONTROL.md`): the scheduler hands each
worker thread a batch sorted by priority, so two of our probes recorded in one entry are in fee
order in every pair we observed; threads then commit their entries in the order they finish, so
across entries fee order drops towards a coin flip. Probes that arrive 10–20 ms apart are handled
in arrival order: each arrives in its own scheduling pass and nothing competes with it. Rakurai
ships a closed scheduler library; its behaviour is indistinguishable from this pattern.

**Firedancer.** Pack compares reward per compute unit exactly and runs one transaction per
microblock, so probes waiting together are in fee order across the block, not only inside an
entry; probes that arrive apart are taken as they come, because pack schedules continuously.
Ties go the other way than on Agave: the newer of two equal-fee probes comes first, which is what
pack's treap does with an equal key. The v1-before-legacy result at "equal fee" is the same strictness at work: a
legacy probe carries compute-budget instructions, which add a few bytes and so about 0.05% to its
cost; Agave's scheduler is too coarse to see that, pack is not.

**BAM (AgaveBam, FireBAM).** Probes sent together are in fee order, and probes spread over a sweep
are ordered in time slices of tens of milliseconds with fee order inside each slice: a batch
auction run by the BAM node, which then hands the validator a sequence it executes as given.
Probes sent a few slots before a BAM validator's window often landed in the block of the BAM
leader before it: the node serves whichever connected validator leads.

**Harmonic.** The rule belongs to the builder strategy each validator chose, not to its client.
Most Harmonic validators put our probes sent together in fee order; at least two put them in send
order whatever the fee (a `fifo` strategy). Probes arriving 10–20 ms apart are handled mostly in
arrival order on both Harmonic clients; on HarmonicFiredancer the best fit is short batches of
about 20 ms. The per-validator table below shows each one.


### Per validator

<!-- results:per_validator:start -->
| Validator | Family | Stake | Windows | Landed probes | Queued together | Ties | Over time | Best single rule | Strictness: concordance [CI], θ | Component | Verdict | Smallest detectable concordance |
| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| [`5Cch…q9Ux`](https://solscan.io/account/5Cchr1XGEg7dbBXByV5NY2ad8jfxAM7HA3x8D56rq9Ux) | AgaveBam | 1.3% | 31 | 262 | priority: 92.3% [88.2, 95.6] | none detectable: p = 0.512, detectable from 75.0% | scheduler passes, w = 5 ms: w within 5 ms–20 ms | priority, ties newest first (p = 0.001); ΔAIC ≤ 2 to priority, ties oldest first | 74.3% [60.8, 86.0], θ 0.45 [0.17, 0.88] | probes queued together are sorted by priority | Priority among probes queued together (92% of pairs), priority within passes of about 5 ms, passes in arrival order, no rule detectable for ties (p=0.51; 80% power needs concordance >= 0.75). | 59.4% |
| [`Fc6N…ULGD`](https://solscan.io/account/Fc6NNdS2j3EmrWbU6Uqt6wsKB5ef72NjaWfNxKYbULGD) | AgaveBam | 1.0% | 28 | 225 | priority: 95.5% [92.7, 97.6] | newest first: 71.2% [49.1, 92.9] | scheduler passes, w = 5 ms: w within 5 ms–20 ms | priority, ties newest first (p = 0.001) | 84.8% [73.5, 93.2], θ 0.84 [0.47, 1.46] | probes queued together are sorted by priority | Priority among probes queued together (96% of pairs), priority within passes of about 5 ms, passes in arrival order, ties newest first (71%). | 61.5% |
| [`5EhG…ULeC`](https://solscan.io/account/5EhGYUyQNrxgUbuYF4vbL2SZDT6RMfhq3yjeyevvULeC) | AgaveBam | 0.8% | 25 | 214 | priority: 92.0% [85.3, 96.9] | none detectable: p = 0.692, detectable from 75.0% | scheduler passes, w = 5 ms: w within 5 ms–20 ms | batch auction, w = 5 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 10 ms, batch auction, w = 20 ms | 79.9% [71.8, 87.6], θ 0.61 [0.38, 0.96] | probes queued together are sorted by priority | Priority among probes queued together (92% of pairs), priority within passes of about 5 ms, passes in arrival order, no rule detectable for ties (p=0.69; 80% power needs concordance >= 0.75). | 62.3% |
| [`DRpb…21hy`](https://solscan.io/account/DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy) | AgaveBam | 3.0% | 10 | 82 | priority: 90.2% [76.4, 97.9] | newest first: 85.7% [82.1, 89.3] | batch auction, w = 40 ms: w within 40 ms–50 ms | batch auction, w = 40 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 50 ms | 74.0% [51.7, 89.9], θ 0.42 [0.03, 1.07] | probes queued together are sorted by priority; arrivals are grouped into ~40 ms batches (a batch auction) | Priority among probes queued together (90% of pairs), batch auction of about 40 ms (batches in arrival order, priority inside), ties newest first (86%). | 68.4% |
| [`9rkJ…NDSQ`](https://solscan.io/account/9rkJMARqK6VBkcxGfKBAwnA44gPAfGxPbPsfsggFNDSQ) | AgaveBam | 1.1% | 10 | 72 | priority: 92.1% [82.8, 99.1] | none detectable: p = 0.548, detectable from 93.4% | send order: 86.9% [73.3, 100.0] | batch auction, w = 30 ms (p = 0.001) | 81.3% [65.9, 91.6], θ 0.74 [0.30, 1.38] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (92% of pairs), arrival order across scheduling passes (sweep 87% send order), no rule detectable for ties (p=0.55; 80% power needs concordance >= 0.93). | 73.9% |
| [`Fyrw…6d4j`](https://solscan.io/account/FyrwfMaomErzqrFUXMjCJ7mA4u81DsiDdrzC3MJD6d4j) | AgaveBam | 0.1% | 1 | 8 | none detectable: p = 0.419 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.428); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 80.0% [80.0, 80.0], θ 1.25 [1.25, 1.25] | none detectable | No rule detectable among probes queued together (p=0.42; too few pairs for 80% power at any tested strength). | – |
| [`2GUn…1rSx`](https://solscan.io/account/2GUnfxZavKoPfS9s3VSEjaWDzB3vNf5RojUhprCS1rSx) | AgaveBam | 0.6% | 2 | 6 | none detectable: p = 0.358 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.347); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.36; too few pairs for 80% power at any tested strength). | – |
| [`9Xhm…7ct7`](https://solscan.io/account/9XhmwxwWtoR6oD4xpSHutoTXku8hXyq76XoTKFZb7ct7) | AgaveBam | 0.0% | 2 | 6 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`BJvr…mTca`](https://solscan.io/account/BJvrWSfonXnS2Km8iA9KLY6D6vS3GcsaUwUNPFBumTca) | AgaveBam | 0.0% | 1 | 6 | none detectable: p = 0.520 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.488); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.52; too few pairs for 80% power at any tested strength). | – |
| [`BUv4…RyyB`](https://solscan.io/account/BUv44cVtsdvU9z2BfFGk6s5JZZWrmVnq5qCaii5ARyyB) | AgaveBam | 0.4% | 2 | 6 | none detectable: p = 0.505 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.491); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.51; too few pairs for 80% power at any tested strength). | – |
| [`GBQ2…AAAA`](https://solscan.io/account/GBQ2GvTzmjXMu97dr7WUnLKYTNim1aoZsYS53KYXAAAA) | AgaveBam | 0.6% | 1 | 6 | none detectable: p = 0.506 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.516); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.51; too few pairs for 80% power at any tested strength). | – |
| [`PAWs…YAZ6`](https://solscan.io/account/PAWsME7oYbjt5TRNc11mBa33JhKnQr9AYherdr9YAZ6) | AgaveBam | 0.0% | 1 | 6 | none detectable: p = 0.246 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.252) | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.25; too few pairs for 80% power at any tested strength). | – |
| [`kyzz…1xgj`](https://solscan.io/account/kyzzzgRymGpePUsLyr48kQHt53kh5CSfRH1qfvz1xgj) | AgaveBam | 0.0% | 1 | 6 | none detectable: p = 0.661 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.680); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 75.0% [75.0, 75.0], θ 0.96 [0.96, 0.96] | none detectable | No rule detectable among probes queued together (p=0.66; too few pairs for 80% power at any tested strength). | – |
| [`5d9M…UWwG`](https://solscan.io/account/5d9Mdc2Zk8as8GL1AxQeXxv5htBBvC5bjfmsXC7UUWwG) | AgaveBam | 0.1% | 1 | 5 | none detectable: p = 0.488 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.508); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.49; too few pairs for 80% power at any tested strength). | – |
| [`Ninj…rmjs`](https://solscan.io/account/Ninja1spj6n9t5hVYgF3PdnYz2PLnkt7rvaw3firmjs) | AgaveBam | 0.3% | 1 | 5 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 0.0% [0.0, 0.0], θ 0.00 [0.00, 0.00] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`bkpk…iR8Z`](https://solscan.io/account/bkpk9KVsDRfrArzzmkJ9mPEvbXfQxczzQYR3QMGiR8Z) | AgaveBam | 0.1% | 1 | 5 | none detectable: p = 0.510 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.510); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.51; too few pairs for 80% power at any tested strength). | – |
| [`6y7V…syrK`](https://solscan.io/account/6y7V8dL673XFzm9QyC5vvh3itWkp7wztahBd2yDqsyrK) | AgaveBam | 0.7% | 1 | 4 | none detectable: p = 0.326 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.348); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.33; too few pairs for 80% power at any tested strength). | – |
| [`7CR3…AqCT`](https://solscan.io/account/7CR3Jq4ny2tsr3DX3DvyjoU8TYs776MGkU6nLMWjAqCT) | AgaveBam | 0.1% | 1 | 4 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`Aw5w…k1ex`](https://solscan.io/account/Aw5wEMXhbygFLR7jHtHpih8QvxVBGAMTqsQ2SjWPk1ex) | AgaveBam | 0.6% | 1 | 4 | none detectable: p = 0.509 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.512); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.51; too few pairs for 80% power at any tested strength). | – |
| [`Cu9L…Rq86`](https://solscan.io/account/Cu9Ls6dsTL6cxFHZdStHwVSh1uy2ynXz8qPJMS5FRq86) | AgaveBam | 0.1% | 1 | 4 | none detectable: p = 0.264 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.258); ΔAIC ≤ 2 to priority, ties oldest first, priority, ties newest first, none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.26; too few pairs for 80% power at any tested strength). | – |
| [`GEM1…UReR`](https://solscan.io/account/GEM1N1UE3C8BB8EaaEPrcFvT3iLMVrurknjW5AYjUReR) | AgaveBam | 0.2% | 1 | 4 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`ciTy…6cQj`](https://solscan.io/account/ciTyjzN9iyobidMycjyqRRM7vXAHXkFzH3m8vEr6cQj) | AgaveBam | 0.0% | 1 | 4 | none detectable: p = 0.251 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.227); ΔAIC ≤ 2 to priority, ties oldest first, priority, ties newest first, none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.25; too few pairs for 80% power at any tested strength). | – |
| [`5ess…4dRn`](https://solscan.io/account/5essVpBYvocZkQnkrWsMDfRPwtp4BeihJRaweKLR4dRn) | AgaveBam | 0.2% | 1 | 2 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 0.0% [0.0, 0.0], θ 0.00 [0.00, 0.00] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`HEL1…e2TU`](https://solscan.io/account/HEL1USMZKAL2odpNBj2oCjffnFGaYwmbGmyewGv1e2TU) | JitoLabs | 3.8% | 66 | 526 | priority: 100.0% in one entry, 71.2% across entries | oldest first: 69.7% [61.0, 78.4] | send order: 98.8% [96.4, 100.0] | batch auction, w = 10 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 5 ms | 80.1% [73.6, 85.9], θ 0.65 [0.45, 0.91] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 71% across entries), arrival order across scheduling passes (sweep 99% send order), ties oldest first (70%); non-conflicting probes are interleaved by parallel execution (73% in rule order vs 95% when they conflict). | 58.8% |
| [`E1r4…dxHL`](https://solscan.io/account/E1r4Psq84tHfQ6aPTvvDka4U3u8zPVD7gEUrH25RdxHL) | JitoLabs | 2.4% | 62 | 512 | priority: 100.0% in one entry, 62.8% across entries | oldest first: 76.8% [65.4, 87.5] | send order: 100.0% [100.0, 100.0] | batch auction, w = 5 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 10 ms | 76.1% [67.7, 83.6], θ 0.50 [0.29, 0.77] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 63% across entries), arrival order across scheduling passes (sweep 100% send order), ties oldest first (77%); non-conflicting probes are interleaved by parallel execution (74% in rule order vs 88% when they conflict). | 59.3% |
| [`C8Be…JP1k`](https://solscan.io/account/C8Bey3LKVJHVqN6xPTeW8WJfUgFQAeGNBpT4Rp99JP1k) | JitoLabs | 2.1% | 37 | 302 | priority: 39.8% [19.6, 62.5] | none detectable: p = 0.656, detectable from 66.3% | batch auction, w = 75 ms | batch auction, w = 5 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 10 ms | 59.6% [44.5, 72.8], θ 0.16 [0.00, 0.40] | probes queued together are sorted by priority; arrivals are grouped into ~75 ms batches (a batch auction) | Priority among probes queued together (40% of pairs), batch auction of about 75 ms (batches in arrival order, priority inside), no rule detectable for ties (p=0.66; 80% power needs concordance >= 0.66); non-conflicting probes are interleaved by parallel execution (23% in rule order vs 80% when they conflict). | 59.2% |
| [`8gDf…2LoM`](https://solscan.io/account/8gDfqHXZ5ektqQQrLrCGtSzYRwkWQrm3ANnAvQCt2LoM) | JitoLabs | 0.4% | 2 | 8 | none detectable: p = 0.229 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.239) | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.23; too few pairs for 80% power at any tested strength). | – |
| [`5pPR…HzSm`](https://solscan.io/account/5pPRHniefFjkiaArbGX3Y8NUysJmQ9tMZg3FrFGwHzSm) | JitoLabs | 1.4% | 1 | 7 | none detectable: p = 0.092 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.088) | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.09; too few pairs for 80% power at any tested strength). | – |
| [`Gv9g…MsWV`](https://solscan.io/account/Gv9gguvrAkgQtB5g5a3Un7trcHCxLYsk8vSojLmQMsWV) | JitoLabs | 0.1% | 1 | 6 | none detectable: p = 0.504 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.500); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.50; too few pairs for 80% power at any tested strength). | – |
| [`ABC1…thwY`](https://solscan.io/account/ABC1U4cf9DZMwqy8ktEr4WJj8VHmVBQibbC57gEJthwY) | JitoLabs | 0.1% | 1 | 5 | none detectable: p = 0.493 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.495); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.49; too few pairs for 80% power at any tested strength). | – |
| [`A9mv…8FAC`](https://solscan.io/account/A9mvukTd77EbRoBX4ydSCFQHdu5bsRFkNXTTRstA8FAC) | JitoLabs | 0.3% | 1 | 4 | none detectable: p = 0.507 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.537); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.51; too few pairs for 80% power at any tested strength). | – |
| [`FbYX…uT4k`](https://solscan.io/account/FbYX2uN573G5WsgiPdHU6fS5PNUyjdXfGfpZNkYUuT4k) | JitoLabs | 0.6% | 1 | 4 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`Ha1i…qJJU`](https://solscan.io/account/Ha1iade1AH3B12K9SccfWoPdFtQKKQsj2ZyWwxcjqJJU) | JitoLabs | 0.2% | 1 | 4 | none detectable: p = 0.335 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.334); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.34; too few pairs for 80% power at any tested strength). | – |
| [`LeDb…uE3V`](https://solscan.io/account/LeDbQ99QT342j9S5YdyXLrsq2Gu3T3dMGajExdAuE3V) | JitoLabs | 0.1% | 1 | 4 | none detectable: p = 0.334 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.323); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.33; too few pairs for 80% power at any tested strength). | – |
| [`UPSC…GubF`](https://solscan.io/account/UPSCQNqdbiaqrQou9X9y8mr43ZHzvoNpKC26Mo7GubF) | JitoLabs | 0.1% | 1 | 4 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 50.0% [50.0, 50.0], θ 0.00 [0.00, 0.00] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`EUcJ…XctB`](https://solscan.io/account/EUcJwf7jXskRE6NZBtFPVH2EedNvNYko8LL2WT62XctB) | JitoLabs | 0.4% | 1 | 3 | none detectable: p = 0.491 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.517); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.49; too few pairs for 80% power at any tested strength). | – |
| [`vu1s…TC2D`](https://solscan.io/account/vu1sGn2f1Xim6voHNLt4nLn38zNkYdLasU7hEr1TC2D) | JitoLabs | 0.2% | 1 | 2 | none detectable: p = 0.516 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.506); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.52; too few pairs for 80% power at any tested strength). | – |
| [`CAo1…Sve4`](https://solscan.io/account/CAo1dCGYrB6NhHh5xb1cGjUiu86iyCfMTENxgHumSve4) | HarmonicAgave | 2.2% | 50 | 432 | priority: 69.9% [62.8, 77.5] | none detectable: p = 0.897, detectable from 66.4% | send order: 91.3% [86.7, 93.9] | priority, ties oldest first (p = 0.001); ΔAIC ≤ 2 to priority, ties newest first | 58.9% [50.8, 66.3], θ 0.14 [0.01, 0.27] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (70% of pairs), arrival order across scheduling passes (sweep 91% send order), no rule detectable for ties (p=0.90; 80% power needs concordance >= 0.66); non-conflicting probes are interleaved by parallel execution (55% in rule order vs 83% when they conflict). | 59.5% |
| [`Btsm…DAaH`](https://solscan.io/account/BtsmiEEvnSuUnKxqXj2PZRYpPJAc7C34mGz8gtJ1DAaH) | HarmonicAgave | 0.9% | 28 | 240 | priority: 69.7% [61.3, 78.4] | none detectable: p = 0.564, detectable from 74.3% | send order: 86.0% [84.8, 88.2] | batch auction, w = 50 ms (p = 0.001) | 66.7% [59.7, 74.1], θ 0.29 [0.16, 0.47] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (70% of pairs), arrival order across scheduling passes (sweep 86% send order), no rule detectable for ties (p=0.56; 80% power needs concordance >= 0.74); non-conflicting probes are interleaved by parallel execution (53% in rule order vs 84% when they conflict). | 61.8% |
| [`Ekvd…6C1f`](https://solscan.io/account/EkvdKhULbMFqjKBKotAzGi3kwMvMpYNDKJXXQQmi6C1f) | HarmonicAgave | 0.9% | 25 | 204 | priority: 72.3% [62.4, 81.8] | none detectable: p = 0.850, detectable from 66.4% | batch auction, w = 40 ms | batch auction, w = 40 ms (p = 0.001) | 67.6% [58.7, 77.0], θ 0.32 [0.15, 0.55] | probes queued together are sorted by priority; arrivals are grouped into ~40 ms batches (a batch auction) | Priority among probes queued together (72% of pairs), batch auction of about 40 ms (batches in arrival order, priority inside), no rule detectable for ties (p=0.85; 80% power needs concordance >= 0.66); non-conflicting probes are interleaved by parallel execution (56% in rule order vs 76% when they conflict). | 61.4% |
| [`Fd7b…69Nk`](https://solscan.io/account/Fd7btgySsrjuo25CJCj7oE7VPMyezDhnx7pZkj2v69Nk) | HarmonicAgave | 3.9% | 20 | 158 | priority: 66.9% [58.4, 76.1] | none detectable: p = 0.821, detectable from 74.6% | send order: 89.6% [87.9, 93.3] | batch auction, w = 10 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 20 ms, batch auction, w = 5 ms | 69.8% [59.2, 79.3], θ 0.35 [0.16, 0.58] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (67% of pairs), arrival order across scheduling passes (sweep 90% send order), no rule detectable for ties (p=0.82; 80% power needs concordance >= 0.75). | 67.3% |
| [`FBKF…jeNi`](https://solscan.io/account/FBKFWadXZJahGtFitAsBvbqh5968gLY7dMBBJUoUjeNi) | HarmonicAgave | 0.9% | 8 | 65 | priority: 76.6% [60.0, 91.5] | none detectable: p = 0.731, detectable from 87.6% | send order: 85.5% [85.5, 85.5] | batch auction, w = 40 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 10 ms, batch auction, w = 5 ms | 74.9% [59.7, 86.9], θ 0.48 [0.17, 0.94] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (77% of pairs), arrival order across scheduling passes (sweep 85% send order), no rule detectable for ties (p=0.73; 80% power needs concordance >= 0.88); non-conflicting probes are interleaved by parallel execution (61% in rule order vs 93% when they conflict). | 75.7% |
| [`5Zqv…q9qk`](https://solscan.io/account/5ZqveVffQPiUbkjBg4KD9kib1MKHLqiFno4ke9jSq9qk) | HarmonicAgave | 0.9% | 5 | 23 | send order: 76.7% [74.4, 100.0] | oldest first: 82.1% [82.1, 82.1] | no data | arrival order (FIFO) (p = 0.002) | 79.7% [75.6, 100.0], θ 0.67 [0.53, ≥ 12] | arrival order: no priority pass reached our probes | Send order among probes queued together (77% of pairs), ties oldest first (82%). | 81.8% |
| [`shft…Vikj`](https://solscan.io/account/shftkxnsXmqAkmLgz9Mn7bNB5Fr6mKgFc58kFHfVikj) | HarmonicAgave | 0.8% | 3 | 19 | none detectable: p = 0.057, detectable from 93.0% | none detectable: p = 1.000, detectable from 87.6% | no data | none detectable; closest priority, ties oldest first (p = 0.111); ΔAIC ≤ 2 to priority, ties oldest first, priority, ties newest first | 64.9% [0.0, 82.1], θ 0.27 [0.00, 0.73] | none detectable | No rule detectable among probes queued together (p=0.06; 80% power needs concordance >= 0.93), no rule detectable for ties (p=1.00; 80% power needs concordance >= 0.88). | 82.3% |
| [`2m1A…oAAb`](https://solscan.io/account/2m1A2WM1vte7RWz5xTTw4i1SiXmngVtXhqFERaUjoAAb) | HarmonicAgave | 0.5% | 4 | 15 | send order: 100.0% [100.0, 100.0] | no data | no data | arrival order (FIFO) (p = 0.027) | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | arrival order: no priority pass reached our probes | Send order among probes queued together (100% of pairs). | 95.3% |
| [`9iFP…nz8R`](https://solscan.io/account/9iFPQbP1jGkj67sXg6YLLGRUBVEDMcapdS6jmCZSnz8R) | HarmonicAgave | 0.1% | 2 | 13 | none detectable: p = 0.504 | none detectable: p = 0.382, detectable from 87.6% | no data | none detectable; closest arrival order (FIFO) (p = 0.201); ΔAIC ≤ 2 to arrival order (FIFO) | 66.7% [64.3, 100.0], θ 0.32 [0.26, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.50; too few pairs for 80% power at any tested strength), no rule detectable for ties (p=0.38; 80% power needs concordance >= 0.88). | 86.6% |
| [`A1vq…ANy4`](https://solscan.io/account/A1vqhA2fS6K7CvHsJKX1ACcHJFEmyRg4KuR5pctHANy4) | HarmonicAgave | 0.9% | 2 | 12 | none detectable: p = 0.319, detectable from 87.1% | no data | no data | none detectable; closest priority, ties oldest first (p = 0.331); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 65.5% [64.3, 100.0], θ 0.29 [0.26, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.32; 80% power needs concordance >= 0.87). | 87.1% |
| [`3ytg…9gpJ`](https://solscan.io/account/3ytgyJXpm3teGLwkHqp9h2qqYNpY4Z1QpWAeVpz79gpJ) | HarmonicAgave | 0.2% | 1 | 8 | none detectable: p = 0.053 | no data | no data | arrival order (FIFO) (p = 0.048) | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | arrival order: no priority pass reached our probes | No rule detectable among probes queued together (p=0.05; too few pairs for 80% power at any tested strength). | 96.7% |
| [`G3a3…KkBQ`](https://solscan.io/account/G3a3iYZKNLbivothF3twqcaTCEvoPb9uJ2bFc9DNKkBQ) | HarmonicAgave | 0.3% | 1 | 8 | none detectable: p = 0.389, detectable from 87.6% | no data | no data | none detectable; closest priority, ties oldest first (p = 0.344); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 64.3% [64.3, 64.3], θ 0.26 [0.26, 0.26] | none detectable | No rule detectable among probes queued together (p=0.39; 80% power needs concordance >= 0.88). | 87.6% |
| [`9UM8…Jd9i`](https://solscan.io/account/9UM8wQ8F5oMiRcP5YdqD6Lr4krpBWCD8LtgQYoisJd9i) | HarmonicAgave | 0.9% | 2 | 6 | none detectable: p = 0.260 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.259); ΔAIC ≤ 2 to priority, ties oldest first, priority, ties newest first, none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.26; too few pairs for 80% power at any tested strength). | – |
| [`CW9C…cd1A`](https://solscan.io/account/CW9C7HBwAMgqNdXkNgFg9Ujr3edR2Ab9ymEuQnVacd1A) | HarmonicAgave | 0.4% | 1 | 5 | no data | no data | none detectable: p = 0.145, detectable from 98.0% | none detectable; closest priority, ties oldest first (p = 0.147); ΔAIC ≤ 2 to priority, ties newest first | 90.0% [90.0, 90.0], θ 1.50 [1.50, 1.50] | none detectable | No rule detectable over time (p=0.14; 80% power needs concordance >= 0.98). | 98.0% |
| [`Stak…itLh`](https://solscan.io/account/Stakex4B2tpDHPWGvV1dninfiaYCGdakgTknpzPitLh) | HarmonicAgave | 0.0% | 1 | 5 | none detectable: p = 0.499 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.480); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.50; too few pairs for 80% power at any tested strength). | – |
| [`4k6w…5zSF`](https://solscan.io/account/4k6wgP5WPBKQpsFGtzuXNrjcTE2fKWLj17nDvFeG5zSF) | HarmonicAgave | 0.2% | 1 | 4 | none detectable: p = 0.528 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.534); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.53; too few pairs for 80% power at any tested strength). | – |
| [`9W3Q…Wws7`](https://solscan.io/account/9W3QTgBhkU4Bwg6cwnDJo6eGZ9BtZafSdu1Lo9JmWws7) | HarmonicAgave | 0.3% | 1 | 4 | none detectable: p = 0.501 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.498); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.50; too few pairs for 80% power at any tested strength). | – |
| [`Ex1A…XAaT`](https://solscan.io/account/Ex1AxFCipXGfSxgvXPPT3nPQUARddCduHwKR6jHiXAaT) | HarmonicAgave | 0.2% | 1 | 4 | none detectable: p = 0.499 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.518); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.50; too few pairs for 80% power at any tested strength). | – |
| [`AMuk…6DJF`](https://solscan.io/account/AMukCLCr52XxsEjXoDxKKxjNg4FpnsReXNaQx8aR6DJF) | HarmonicAgave | 0.3% | 1 | 2 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 0.0% [0.0, 0.0], θ 0.00 [0.00, 0.00] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`JUPi…1h4b`](https://solscan.io/account/JUPiTERrZqgf1jUyR7dSkhMx4Kn2qJyekWsg3LT1h4b) | HarmonicFiredancer | 2.2% | 46 | 394 | priority: 83.2% [73.7, 91.6] | none detectable: p = 0.535, detectable from 66.4% | batch auction, w = 20 ms | priority, ties oldest first (p = 0.001); ΔAIC ≤ 2 to priority, ties newest first | 70.1% [60.5, 79.3], θ 0.35 [0.17, 0.58] | probes queued together are sorted by priority; arrivals are grouped into ~20 ms batches (a batch auction) | Priority among probes queued together (83% of pairs), batch auction of about 20 ms (batches in arrival order, priority inside), no rule detectable for ties (p=0.54; 80% power needs concordance >= 0.66); v1 ahead at equal fee (100%). | 59.4% |
| [`Awes…vpLM`](https://solscan.io/account/Awes4Tr6TX8JDzEhCZY2QVNimT6iD1zWHzf1vNyGvpLM) | HarmonicFiredancer | 1.6% | 42 | 357 | priority: 92.0% [87.3, 95.9] | none detectable: p = 0.822, detectable from 75.0% | send order: 86.0% [78.1, 92.0] | priority, ties newest first (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 10 ms, batch auction, w = 150 ms, batch auction, w = 200 ms, batch auction, w = 30 ms, batch auction, w = 50 ms, batch auction, w = 5 ms, priority, ties oldest first | 70.7% [58.6, 82.2], θ 0.38 [0.15, 0.71] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (92% of pairs), arrival order across scheduling passes (sweep 86% send order), no rule detectable for ties (p=0.82; 80% power needs concordance >= 0.75); v1 ahead at equal fee (100%). | 59.0% |
| [`Drif…xatz`](https://solscan.io/account/DrifTrN923QaouP89UxkQzFGbumKPCnfkNYQRwmZxatz) | HarmonicFiredancer | 0.7% | 17 | 152 | priority: 96.4% [93.0, 98.7] | none detectable: p = 0.586, detectable from 75.0% | send order: 87.2% [84.6, 89.1] | priority, ties newest first (p = 0.001); ΔAIC ≤ 2 to priority, ties oldest first | 68.2% [48.2, 85.9], θ 0.31 [0.00, 0.84] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (96% of pairs), arrival order across scheduling passes (sweep 87% send order), no rule detectable for ties (p=0.59; 80% power needs concordance >= 0.75). | 62.5% |
| [`EvnR…qDo4`](https://solscan.io/account/EvnRmnMrd69kFdbLMxWkTn1icZ7DCceRhvmb2SJXqDo4) | HarmonicFiredancer (also JitoLabs) | 1.8% | 9 | 75 | priority: 80.9% [44.3, 96.6] | none detectable: p = 0.552, detectable from 87.6% | send order: 93.3% [90.0, 96.7] | batch auction, w = 20 ms (p = 0.001) | 82.3% [63.0, 95.2], θ 0.82 [0.26, 1.92] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (81% of pairs), arrival order across scheduling passes (sweep 93% send order), no rule detectable for ties (p=0.55; 80% power needs concordance >= 0.88). | 73.0% |
| [`6ptu…JwWG`](https://solscan.io/account/6ptuwW4rg5A3wP9hNKxeymG3QhSagugPd3grnMTGJwWG) | HarmonicFiredancer | 0.3% | 2 | 16 | priority: 92.9% [92.9, 92.9] | none detectable: p = 0.553, detectable from 87.6% | no data | priority, ties newest first (p = 0.004); ΔAIC ≤ 2 to priority, ties oldest first | 76.8% [60.7, 92.9], θ 0.55 [0.19, 1.44] | probes queued together are sorted by priority | Priority among probes queued together (93% of pairs), no rule detectable for ties (p=0.55; 80% power needs concordance >= 0.88). | 82.6% |
| [`97jb…eeMt`](https://solscan.io/account/97jbhVBYcSmwGXjrx5PPWXucDsVBqwyoQ6rzP3B6eeMt) | HarmonicFiredancer | 0.0% | 2 | 16 | priority: 100.0% [100.0, 100.0] | no data | no data | priority, ties oldest first (p = 0.001); ΔAIC ≤ 2 to priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | probes queued together are sorted by priority | Priority among probes queued together (100% of pairs). | 82.6% |
| [`CQdL…ow12`](https://solscan.io/account/CQdLgwkmmHF8DE6t4sguXMTXWDxq3oLh3xfekDTmow12) | HarmonicFiredancer | 0.5% | 2 | 15 | priority: 81.8% [0.0, 96.4] | no data | no data | priority, ties oldest first (p = 0.001); ΔAIC ≤ 2 to priority, ties newest first | 81.8% [0.0, 96.4], θ 0.80 [0.00, 2.04] | probes queued together are sorted by priority | Priority among probes queued together (82% of pairs). | 85.7% |
| [`GZ4U…vp5H`](https://solscan.io/account/GZ4U21pYmFkCk6ZNTHRzfVEZZXauyZLKDmuuuKGwvp5H) | HarmonicFiredancer | 0.2% | 1 | 8 | no data | none detectable: p = 0.558, detectable from 87.6% | no data | none detectable; closest arrival order (FIFO) (p = 0.539); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 60.7% [60.7, 60.7], θ 0.19 [0.19, 0.19] | none detectable | No rule detectable for ties (p=0.56; 80% power needs concordance >= 0.88). | 87.6% |
| [`HnrX…xTYA`](https://solscan.io/account/HnrXmm65CyvmSyvwEMjmyHzYjAyhHiHEJ7s1pf6BxTYA) | HarmonicFiredancer | 0.1% | 1 | 8 | priority: 100.0% [100.0, 100.0] | no data | no data | priority, ties oldest first (p = 0.001); ΔAIC ≤ 2 to priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | probes queued together are sorted by priority | Priority among probes queued together (100% of pairs). | 87.6% |
| [`pSo1…nkfc`](https://solscan.io/account/pSo1KZXgG1EGHch6pPmscfYhv3buXyaj2dR8WkVnkfc) | HarmonicFiredancer | 0.4% | 1 | 8 | priority: 100.0% [100.0, 100.0] | no data | no data | priority, ties oldest first (p = 0.001); ΔAIC ≤ 2 to priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | probes queued together are sorted by priority | Priority among probes queued together (100% of pairs). | 87.6% |
| [`EJsJ…kJk8`](https://solscan.io/account/EJsJx7Smt3jBheNF2ReTxq3HXh9Aczt6PHK12B5tkJk8) | HarmonicFiredancer | 0.1% | 1 | 4 | none detectable: p = 0.541 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.508); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.54; too few pairs for 80% power at any tested strength). | – |
| [`9eGr…8FoY`](https://solscan.io/account/9eGrDohdNTAo61DRHyfMuqKWXqYnA3i254Wiszxe8FoY) | Firedancer | 1.6% | 49 | 423 | priority: 97.5% [94.5, 99.6] | newest first: 87.5% [80.3, 93.6] | send order: 91.4% [89.4, 93.2] | priority, ties newest first (p = 0.001) | 77.5% [64.2, 90.1], θ 0.51 [0.22, 1.11] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (97% of pairs), arrival order across scheduling passes (sweep 91% send order), ties newest first (88%); v1 ahead at equal fee (100%). | 59.8% |
| [`JD54…HybB`](https://solscan.io/account/JD549HsbJHeEKKUrKgg4Fj2iyv2RGjsV7NTZjZUrHybB) | Firedancer | 1.4% | 39 | 329 | priority: 90.7% [80.4, 99.0] | newest first: 88.4% [81.7, 94.6] | scheduler passes, w = 5 ms: w within 5 ms–10 ms, w not identifiable | priority, ties newest first (p = 0.001) | 64.0% [48.9, 79.8], θ 0.23 [0.00, 0.62] | probes queued together are sorted by priority | Priority among probes queued together (91% of pairs), priority within passes of about 5 ms, passes in arrival order, ties newest first (88%); v1 ahead at equal fee (100%). | 59.3% |
| [`DNVZ…eWkf`](https://solscan.io/account/DNVZMSqeRH18Xa4MCTrb1MndNf3Npg4MEwqswo23eWkf) | Firedancer | 0.4% | 21 | 182 | priority: 98.1% [94.0, 100.0] | newest first: 93.8% [88.4, 98.2] | send order: 100.0% [100.0, 100.0] | priority, ties newest first (p = 0.001) | 71.4% [49.7, 92.6], θ 0.38 [0.00, 1.37] | probes queued together are sorted by priority; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (98% of pairs), arrival order across scheduling passes (sweep 100% send order), ties newest first (94%). | 62.1% |
| [`BFmq…Wmzy`](https://solscan.io/account/BFmqBsaGjeYA7gHpReKmnVkRyhqiMmHoR9ttv1crWmzy) | Firedancer | 0.2% | 1 | 4 | none detectable: p = 0.508 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.508); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.51; too few pairs for 80% power at any tested strength). | – |
| [`9jxg…nGFP`](https://solscan.io/account/9jxgosAfHgHzwnxsHw4RAZYaLVokMbnYtmiZBreynGFP) | FireBAM | 1.0% | 31 | 269 | priority: 93.8% [91.0, 96.4] | newest first: 75.0% [57.1, 91.7] | batch auction, w = 20 ms | priority, ties newest first (p = 0.001) | 67.7% [53.4, 81.6], θ 0.29 [0.05, 0.67] | probes queued together are sorted by priority; arrivals are grouped into ~20 ms batches (a batch auction) | Priority among probes queued together (94% of pairs), batch auction of about 20 ms (batches in arrival order, priority inside), ties newest first (75%). | 59.7% |
| [`LA1N…M3wc`](https://solscan.io/account/LA1NEzryoih6CQW3gwQqJQffK2mKgnXcjSQZSRpM3wc) | FireBAM | 0.1% | 5 | 44 | priority: 100.0% [100.0, 100.0] | none detectable: p = 0.307, detectable from 87.6% | no data | priority, ties newest first (p = 0.001); ΔAIC ≤ 2 to priority, ties oldest first | 93.8% [80.7, 100.0], θ 1.46 [0.68, ≥ 12] | probes queued together are sorted by priority | Priority among probes queued together (100% of pairs), no rule detectable for ties (p=0.31; 80% power needs concordance >= 0.88). | 77.0% |
| [`SSmB…oQLY`](https://solscan.io/account/SSmBEooM7RkmyuXxuKgAhTvhQZ36Z3G2WsmLGJKoQLY) | FireBAM | 0.1% | 2 | 20 | priority: 82.1% [82.1, 82.1] | no data | batch auction, w = 20 ms | batch auction, w = 20 ms (p = 0.001) | 89.4% [67.9, 98.5], θ 0.95 [0.33, 2.47] | probes queued together are sorted by priority; arrivals are grouped into ~20 ms batches (a batch auction) | Priority among probes queued together (82% of pairs), batch auction of about 20 ms (batches in arrival order, priority inside). | 78.8% |
| [`qZMH…QrbP`](https://solscan.io/account/qZMH9GWnnBkx7aM1h98iKSv2Lz5N78nwNSocAxDQrbP) | FireBAM | 0.1% | 2 | 20 | no data | newest first: 96.4% [96.4, 96.4] | none detectable: p = 0.087, detectable from 86.3% | priority, ties newest first (p = 0.001) | 83.9% [73.5, 96.4], θ 0.84 [0.50, 2.04] | scheduler priority pass; no reordering by parallel execution detected | No rule detectable over time (p=0.09; 80% power needs concordance >= 0.86), ties newest first (96%). | 81.7% |
| [`ana2…xcfY`](https://solscan.io/account/ana2y2YvQ3ZPMwm6qhnN3nJoUSiT3qx5Pvetkq9xcfY) | Rakurai | 0.6% | 28 | 256 | priority: 100.0% in one entry, 66.4% across entries | none detectable: p = 0.181, detectable from 75.0% | send order: 96.8% [93.4, 99.8] | batch auction, w = 5 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 10 ms | 76.6% [67.9, 84.9], θ 0.48 [0.29, 0.76] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 66% across entries), arrival order across scheduling passes (sweep 97% send order), no rule detectable for ties (p=0.18; 80% power needs concordance >= 0.75); non-conflicting probes are interleaved by parallel execution (60% in rule order vs 95% when they conflict). | 59.9% |
| [`E3mD…4h3F`](https://solscan.io/account/E3mDbFMr8yZcCAZx5wPUFCRmLX31u8Nnj4jPoPc54h3F) | Rakurai | 0.2% | 18 | 151 | priority: 100.0% in one entry, 67.6% across entries | newest first: 63.7% [53.0, 74.4] | scheduler passes, w = 10 ms | batch auction, w = 10 ms (p = 0.001) | 67.7% [52.8, 80.6], θ 0.31 [0.05, 0.63] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches | Priority among probes queued together (100% within an entry, 68% across entries), priority within passes of about 10 ms, passes in arrival order, ties newest first (64%). | 61.9% |
| [`7PdK…ckFf`](https://solscan.io/account/7PdKhpKz7T39vZHFL1UfcYNDsLvay6hp4KPQq1aUckFf) | Rakurai | 0.2% | 5 | 44 | priority: 100.0% in one entry, 86.0% across entries | none detectable: p = 0.907, detectable from 87.6% | no data | priority, ties newest first (p = 0.001); ΔAIC ≤ 2 to priority, ties oldest first | 84.8% [66.4, 96.6], θ 0.77 [0.30, 1.98] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches | Priority among probes queued together (100% within an entry, 86% across entries), no rule detectable for ties (p=0.91; 80% power needs concordance >= 0.88). | 77.0% |
| [`7QQG…wLpE`](https://solscan.io/account/7QQGNm3ptwinipDCyaCF7jY5katgmFUu1ieP2f7nwLpE) | Agave | 0.4% | 23 | 187 | priority: 100.0% in one entry, 77.8% across entries | none detectable: p = 0.228, detectable from 75.0% | send order: 83.7% [73.4, 98.5] | batch auction, w = 5 ms (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 10 ms | 77.2% [70.2, 85.3], θ 0.53 [0.35, 0.85] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 78% across entries), arrival order across scheduling passes (sweep 84% send order), no rule detectable for ties (p=0.23; 80% power needs concordance >= 0.75). | 62.1% |
| [`5Us1…5nBS`](https://solscan.io/account/5Us18hLZPXJTS4QVuGSsUw137Dyd2tgBaem24Xsf5nBS) | Agave | 0.3% | 23 | 178 | priority: 100.0% in one entry, 78.7% across entries | none detectable: p = 0.454, detectable from 75.0% | send order: 100.0% [100.0, 100.0] | priority, ties oldest first (p = 0.001); ΔAIC ≤ 2 to batch auction, w = 75 ms, priority, ties newest first | 59.0% [41.8, 76.8], θ 0.14 [0.00, 0.53] | the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches; probes arriving apart are handled in arrival order (continuous scheduling) | Priority among probes queued together (100% within an entry, 79% across entries), arrival order across scheduling passes (sweep 100% send order), no rule detectable for ties (p=0.45; 80% power needs concordance >= 0.75). | 62.2% |
| [`9hQq…wnTf`](https://solscan.io/account/9hQqNe3DQTiwhspatewA8EXhz12e6sq5UJVJ2qNRwnTf) | Agave | 0.0% | 2 | 12 | none detectable: p = 0.516 | none detectable: p = 0.426, detectable from 87.6% | no data | none detectable; closest arrival order (FIFO) (p = 0.548); ΔAIC ≤ 2 to arrival order (FIFO) | 40.0% [35.7, 100.0], θ 0.00 [0.00, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.52; too few pairs for 80% power at any tested strength), no rule detectable for ties (p=0.43; 80% power needs concordance >= 0.88). | 86.6% |
| [`2FL6…Ypef`](https://solscan.io/account/2FL6757bjpBe1datbq91eKDCp7Jqpce5HC8aZ6eTYpef) | Unknown | 0.0% | 3 | 7 | none detectable: p = 0.493 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.507); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.49; too few pairs for 80% power at any tested strength). | – |
| [`7KVn…jQPN`](https://solscan.io/account/7KVnUL45FH4N8Z7x1dnvJ7GzMx12cs81mS26mHzNjQPN) | Unknown | 0.0% | 1 | 7 | none detectable: p = 0.250 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.233) | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.25; too few pairs for 80% power at any tested strength). | – |
| [`ANCV…6jch`](https://solscan.io/account/ANCVpxEySGWWLqqkVKw2xWYDE9UP4fmZWMCBr5t96jch) | Unknown | 0.0% | 1 | 7 | none detectable: p = 0.154, detectable from 99.7% | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.155); ΔAIC ≤ 2 to none detectable | 81.8% [81.8, 81.8], θ 1.01 [1.01, 1.01] | none detectable | No rule detectable among probes queued together (p=0.15; 80% power needs concordance >= 1.00). | 93.6% |
| [`6aow…VGPs`](https://solscan.io/account/6aow5rTURdbhbeMDrFrbP2GR5vZjMEhktEy87iH1VGPs) | Unknown | 0.0% | 1 | 4 | none detectable: p = 0.522 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.495); ΔAIC ≤ 2 to none detectable | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.52; too few pairs for 80% power at any tested strength). | – |
| [`DbWT…CuEr`](https://solscan.io/account/DbWTEseEDkBQzxbUFaTm9JsLy9FfeEGm1bsrfHSzCuEr) | Unknown | 0.0% | 1 | 3 | none detectable: p = 1.000 | no data | no data | none detectable; closest arrival order (FIFO) (p = 1.000); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=1.00; too few pairs for 80% power at any tested strength). | – |
| [`G83j…2Qys`](https://solscan.io/account/G83jy4NB16A4mchfRjZvfH8SPH2TxwjSZYxh5fzg2Qys) | Unknown | 0.0% | 1 | 2 | none detectable: p = 0.508 | no data | no data | none detectable; closest arrival order (FIFO) (p = 0.517); ΔAIC ≤ 2 to arrival order (FIFO), priority, ties oldest first, priority, ties newest first | 100.0% [100.0, 100.0], θ ≥ 12 [≥ 12, ≥ 12] | none detectable | No rule detectable among probes queued together (p=0.51; too few pairs for 80% power at any tested strength). | – |
<!-- results:per_validator:end -->

## 3. The predictions, checked

Written from the clients' source code and committed before the first transaction
([docs/PREDICTIONS.md](docs/PREDICTIONS.md)).

<!-- results:predictions:start -->
| Id | Prediction | Outcome | Evidence |
| --- | --- | --- | --- |
| P0 | BAM and Harmonic: the gossip tpuQuic is a proxy, so we measure the external orderer | confirmed | proxy tpuQuic in 100.0% of 739 BAM/Harmonic windows (other families 0.0% of 612) |
| P-A1 | Agave/JitoLabs ladder queued before the window: priority order | confirmed | Agave priority (100.0% in one entry, 78.2% across entries); JitoLabs priority (100.0% in one entry, 58.3% across entries); ladders sent before the window: 80.3% [73.7, 86.1] n=2,141 higher priority first |
| P-A2 | Agave/JitoLabs: the shared arm holds priority order more strictly than distinct | confirmed | shared − distinct concordance with priority, ladders: Agave +24.6 pts [+15.0, +33.9]; JitoLabs +35.3 pts [+22.6, +49.1] |
| P-A3 | Agave/JitoLabs tie: oldest first (FIFO) at 2 and 5 ms; no reliable order at 0.5 ms | refuted | Agave none detectable (p = 0.342, detectable from 75.0%); earlier sent first by spacing: 0.5ms 51.8% [35.7, 67.9] (2 batches), 2ms 61.8% [44.4, 78.6] (2 batches), 5ms 51.2% [46.4, 57.1] (3 batches); JitoLabs oldest first (67.1% [60.0, 73.8]); earlier sent first by spacing: 0.5ms 65.4% [56.6, 74.0] (11 batches), 2ms 63.0% [54.9, 71.8] (11 batches), 5ms 74.3% [55.9, 89.3] (9 batches); 0.5 ms is reliably ordered too |
| P-A4 | Agave/JitoLabs ladder arriving mid-window: weaker priority order than P-A1 | confirmed | higher priority first, ladder: before the window 80.3% [73.7, 86.1] n=2,141; in the window 56.1% [43.5, 67.9] n=476; difference +24.2 pts [+10.4, +37.9] |
| P-A5 | Agave/JitoLabs sweep (10–20 ms apart): send order | confirmed | Agave send order (89.8% [79.8, 99.2]); JitoLabs send order (95.5% [88.9, 100.0]) |
| P-A6 | Agave/JitoLabs: inclusion does not depend on fee | confirmed | landed 94.8% at fee 0 vs 94.8% at 10,000 lamports; χ² p = 1.000 over 8 fee levels |
| P-F1 | Firedancer ladder: strict priority order | confirmed | Firedancer priority (95.1% [91.3, 98.2]); concordance 95.1% [91.3, 98.3] (strict = at least 95%) |
| P-F2 | Firedancer tie: newest first (LIFO) when queued together | confirmed | Firedancer newest first (89.0% [85.1, 92.9]) |
| P-F3 | Firedancer sweep: send order | confirmed | Firedancer scheduler passes, w = 5 ms (w not identifiable) |
| P-F4 | Firedancer: pre-window sends are kept until the blockhash expires | confirmed | landed 100.0% of 600 sent before the window vs 96.1% of 380 sent in it; one-sided p = 1.000 |
| P-B1 | BAM sweep: batch auction, batched(w) with w between 20 and 50 ms | confirmed | sweep: AgaveBam batch auction, w = 20 ms (w within 10 ms–20 ms); FireBAM batch auction, w = 20 ms |
| P-B2 | BAM ladder burst: priority order | confirmed | AgaveBam priority (92.0% [89.7, 94.0]); FireBAM priority (94.2% [91.5, 97.0]) |
| P-B3 | BAM: pre-window sends can land in the previous BAM leader's block | confirmed | 61 of 1,263 landed BAM-targeted probes landed before the window; their producers: 22 AgaveBam, 11 HarmonicAgave, 16 JitoLabs, 12 Unknown |
| P-B4 | FireBAM orders like AgaveBam | confirmed | simultaneous: AgaveBam priority, FireBAM priority; ties: AgaveBam newest first, FireBAM newest first; over time: AgaveBam batch auction, w = 20 ms, FireBAM batch auction, w = 20 ms |
| P-H1 | Harmonic validators on the default fba strategy: batched(w ≈ 50 ms) | refuted | 30 Harmonic validators, over time: 20 no data, 7 send order, 1 batch auction, w = 20 ms, 1 batch auction, w = 40 ms, 1 none detectable; 1 of 9 with a detectable rule are batched at 40–75 ms |
| P-H2 | Harmonic validators on fifo: send order regardless of fee | confirmed | 30 Harmonic validators, queued together: 14 priority, 12 none detectable, 2 no data, 2 send order; send order detected for 9 validator(s): 2m1A2W…, 5ZqveV…, Awes4T…, BtsmiE…, CAo1dC…, DrifTr…, EvnRmn…, FBKFWa…, Fd7btg… |
| P-H3 | Harmonic: the validator's own client (Agave vs Firedancer) does not change the order | confirmed | HarmonicAgave priority/batched(40 ms), priority/fifo; HarmonicFiredancer priority/batched(20 ms), priority/fifo (queued together / over time) |
| P-R1 | Rakurai ladder: priority order | confirmed | Rakurai priority (100.0% in one entry, 69.0% across entries) |
| P-X1 | Agave and Firedancer: no order difference between v1 and legacy at equal fee | refuted | v1 first at equal fee: Agave 33.3% [0.0, 50.0] n=12, p = 0.415; Firedancer 100.0% [100.0, 100.0] n=36, p = 0.001; open question: AgaveBam 69.4% [58.3, 83.3]; FireBAM 81.2% [62.5, 100.0]; HarmonicAgave 48.1% [28.8, 67.3]; HarmonicFiredancer 97.9% [93.8, 100.0] |
<!-- results:predictions:end -->

## 4. Scheduler versus parallel execution: entries

<!-- results:entries:start -->
| Family | Pairs in one entry | Pairs across entries | Priority first, same entry | Priority first, across | Tie FIFO, same entry | Tie FIFO, across | Mean entry size | Single-tx entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AgaveBam | 0 | 3,076 | – | 92.0% [89.5, 94.0] | – | 39.6% [29.1, 50.2] | 1.0 | 99.0% |
| JitoLabs | 1,224 | 3,440 | 100.0% [100.0, 100.0] | 58.3% [50.3, 65.8] | 81.4% [74.3, 87.6] | 56.9% [48.0, 65.6] | 1.2 | 87.0% |
| HarmonicAgave | 0 | 4,117 | – | 69.9% [65.9, 73.9] | – | 51.2% [46.6, 56.1] | 1.0 | 98.2% |
| HarmonicFiredancer | 0 | 3,676 | – | 89.7% [85.2, 93.4] | – | 50.6% [44.7, 56.1] | 1.0 | 100.0% |
| Firedancer | 0 | 3,472 | – | 95.1% [90.9, 98.2] | – | 11.2% [7.3, 15.5] | 1.0 | 100.0% |
| FireBAM | 0 | 1,384 | – | 94.2% [91.2, 96.8] | – | 23.2% [10.3, 37.5] | 1.0 | 100.0% |
| Rakurai | 380 | 1,379 | 100.0% [100.0, 100.0] | 69.0% [59.5, 78.4] | 34.3% [26.8, 45.2] | 48.8% [31.9, 63.1] | 1.6 | 75.6% |
| Agave | 296 | 1,040 | 100.0% [100.0, 100.0] | 78.2% [70.5, 85.5] | 54.8% [43.9, 67.6] | 52.5% [45.8, 62.5] | 1.2 | 89.4% |
| Unknown | 0 | 20 | – | 50.0% [33.3, 60.0] | – | – | 1.0 | 97.4% |

_Inside one entry the order is the scheduler's hand-off to one thread; across entries it is the order threads finished._
<!-- results:entries:end -->

On mainnet most entries hold a single transaction (busy blocks give the scheduler little to batch),
so pairs of our probes in one entry occur mostly on local schedulers; for the others the
cross-entry column is the block order itself. Every entry boundary used here was recorded from the
entry stream and is checked by `precedence verify`: it recomputes the hash chain of the block's
entries from the block's transaction signatures (`getBlock`) and requires it to end on the block's
blockhash.


## 5. Does a later, higher fee still win?

Share of probe pairs where the higher-priority probe came first, by how much later it was sent.

<!-- results:dose_response:start -->
| Higher-priority probe was… | sent earlier or at the same time | < 1 ms later | 1–5 ms later | 5–20 ms later | 20–50 ms later | 50–150 ms later | > 150 ms later |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all families | 76.0% [69.4, 81.8] n=770 | 64.9% [56.8, 71.8] n=1,162 | 36.8% [20.0, 58.3] n=19 | 19.4% [13.8, 25.5] n=623 | 9.6% [7.0, 12.6] n=1,657 | 1.3% [0.2, 3.0] n=1,531 | 27.6% [6.5, 48.2] n=116 |
| AgaveBam | 93.0% [86.8, 98.1] n=57 | 86.2% [79.4, 91.4] n=269 | – | 44.4% [0.0, 62.3] n=36 | 4.8% [0.9, 9.6] n=125 | 7.6% [0.0, 28.1] n=119 | 76.2% [51.6, 93.3] n=42 |
| JitoLabs | 70.2% [55.6, 81.9] n=181 | 40.2% [27.6, 52.3] n=189 | 100.0% [100.0, 100.0] n=2 | 7.1% [0.0, 19.1] n=113 | 5.8% [0.0, 15.1] n=292 | 0.4% [0.0, 1.4] n=237 | 0.0% [0.0, 0.0] n=4 |
| HarmonicAgave | 68.0% [54.4, 80.9] n=122 | 70.8% [51.0, 85.9] n=144 | 0.0% [0.0, 0.0] n=1 | 33.3% [25.2, 42.3] n=93 | 13.3% [8.2, 19.2] n=233 | 1.4% [0.0, 4.6] n=217 | 0.0% [0.0, 0.0] n=2 |
| HarmonicFiredancer | 69.1% [45.7, 87.5] n=94 | 60.7% [34.1, 85.6] n=89 | 50.0% [0.0, 100.0] n=2 | 38.2% [31.1, 46.1] n=89 | 11.8% [8.0, 15.9] n=238 | 0.0% [0.0, 0.0] n=198 | 0.0% [0.0, 0.0] n=7 |
| Firedancer | 97.9% [95.1, 100.0] n=97 | 61.8% [28.1, 92.4] n=76 | 0.0% [0.0, 0.0] n=1 | 10.8% [2.5, 27.7] n=74 | 6.6% [1.6, 12.4] n=256 | 0.0% [0.0, 0.0] n=343 | 0.0% [0.0, 0.0] n=51 |
| FireBAM | 91.9% [81.3, 100.0] n=74 | 76.8% [67.5, 83.4] n=168 | 0.0% [0.0, 0.0] n=1 | 35.0% [13.9, 71.4] n=60 | 30.7% [15.5, 56.5] n=150 | 5.3% [0.7, 19.8] n=131 | 0.0% [0.0, 0.0] n=5 |
| Rakurai | 63.5% [48.3, 78.7] n=96 | 53.8% [33.3, 72.2] n=145 | 25.0% [0.0, 33.3] n=4 | 2.8% [0.0, 7.0] n=109 | 3.7% [0.0, 8.7] n=214 | 0.0% [0.0, 0.0] n=172 | 0.0% [0.0, 0.0] n=2 |
| Agave | 67.3% [63.6, 100.0] n=49 | 43.9% [33.3, 50.0] n=82 | 37.5% [37.5, 37.5] n=8 | 0.0% [0.0, 0.0] n=49 | 4.0% [0.0, 13.1] n=149 | 0.0% [0.0, 0.0] n=114 | 0.0% [0.0, 0.0] n=3 |

_Share of pairs where the higher-priority probe came first, by how much later it was sent; 95% interval over batches, n = pairs._
<!-- results:dose_response:end -->

Only batches that reached the leader inside its window are counted here (first send at or after
the window's measured start): arrival can compete with fee only while the leader is building.
Read across a row: when the higher-fee probe was sent at the same time or earlier, it came first in
most pairs on every client. Once it was sent 5 ms or more later, arrival won almost every pair on
local schedulers and Firedancer; on BAM and Harmonic a later higher-fee probe still won a third of
the pairs at 5–20 ms (and on FireBAM at 20–50 ms), the trace of their short batches. Probes that
wait in a leader's queue before its window are a different situation: they are all present when the
leader starts, and are sorted as one burst (section 2, "queued together").


## 6. Equal fees: oldest or newest first

<!-- results:ties:start -->
| Family | Tie pairs | Batches | Earlier sent first | p | Direction | 0.5 ms apart | 2 ms apart | 5 ms apart | Distinct arm | Shared arm |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| AgaveBam | 447 | 16 | 39.6% [28.4, 50.5] | 0.003 | no reliable order | 33.3% [10.7, 57.1] b=3 | 54.7% [44.6, 66.5] b=8 | 19.3% [9.3, 29.3] b=5 | 39.6% [26.8, 53.2] | 39.5% [20.8, 56.6] |
| JitoLabs | 848 | 31 | 67.1% [60.5, 73.9] | 0.001 | oldest first (FIFO) | 65.4% [56.6, 74.0] b=11 | 63.0% [54.9, 71.8] b=11 | 74.3% [55.9, 89.3] b=9 | 61.4% [52.1, 70.8] | 72.9% [63.3, 82.1] |
| HarmonicAgave | 762 | 28 | 51.3% [47.1, 55.6] | 0.659 | no reliable order | 35.7% [26.8, 44.6] b=4 | 51.1% [47.2, 55.6] b=12 | 57.0% [51.0, 63.4] b=12 | 51.1% [45.0, 58.1] | 51.5% [45.6, 57.4] |
| HarmonicFiredancer | 644 | 23 | 50.6% [45.2, 56.2] | 0.862 | no reliable order | 48.5% [40.3, 55.6] b=7 | 48.6% [40.0, 56.4] b=10 | 56.5% [44.0, 70.8] b=6 | 52.1% [43.6, 62.5] | 49.5% [42.0, 56.0] |
| Firedancer | 616 | 22 | 11.0% [7.1, 15.4] | 0.001 | newest first (LIFO) | 15.0% [10.4, 20.0] b=10 | 9.8% [7.1, 15.2] b=4 | 6.7% [0.9, 16.1] b=8 | 10.7% [4.9, 17.9] | 11.4% [6.5, 16.6] |
| FireBAM | 224 | 8 | 23.2% [10.3, 38.4] | 0.001 | newest first (LIFO) | 22.3% [5.4, 44.6] b=4 | 50.0% [50.0, 50.0] b=1 | 15.5% [0.0, 32.1] b=3 | 34.5% [21.4, 50.0] | 16.4% [2.9, 37.9] |
| Rakurai | 307 | 11 | 42.0% [32.5, 51.1] | 0.074 | no reliable order | 41.0% [25.0, 50.0] b=3 | 48.8% [46.4, 53.6] b=3 | 38.6% [22.1, 55.8] b=5 | 45.1% [34.4, 55.6] | 36.6% [21.4, 51.8] |
| Agave | 195 | 7 | 54.4% [45.1, 65.3] | 0.443 | no reliable order | 51.8% [35.7, 67.9] b=2 | 61.8% [44.4, 78.6] b=2 | 51.2% [46.4, 57.1] b=3 | 57.1% [46.4, 67.9] | 53.2% [42.0, 66.2] |

_Earlier sent first: share of equal-fee pairs where the earlier-sent probe came first; by spacing from the rule card, b = batches._
<!-- results:ties:end -->

## 7. Over time: arrival order, priority, or batches

<!-- results:sweep:start -->
| Validator | Family | Over-time rule (rule card) | Best batch length w (ΔAIC ≤ 2 range) | w identifiable | Sweep: higher priority first | Sweep: earlier sent first | Batched check p | Best single rule, all batches |
| --- | --- | --- | ---: | --- | ---: | ---: | --- | --- |
| [`5Cch…q9Ux`](https://solscan.io/account/5Cchr1XGEg7dbBXByV5NY2ad8jfxAM7HA3x8D56rq9Ux) | AgaveBam | scheduler passes, w = 5 ms: w within 5 ms–20 ms | 5 ms (5 ms–20 ms) | yes | 55.3% [21.5, 82.3] n=329 | 45.0% [16.3, 78.5] | 0.005 (kept) | priority, ties newest first |
| [`5EhG…ULeC`](https://solscan.io/account/5EhGYUyQNrxgUbuYF4vbL2SZDT6RMfhq3yjeyevvULeC) | AgaveBam | scheduler passes, w = 5 ms: w within 5 ms–20 ms | 5 ms (5 ms–20 ms) | yes | 54.5% [20.4, 79.4] n=312 | 46.0% [21.5, 80.9] | 0.005 (kept) | batch auction, w = 5 ms |
| [`9rkJ…NDSQ`](https://solscan.io/account/9rkJMARqK6VBkcxGfKBAwnA44gPAfGxPbPsfsggFNDSQ) | AgaveBam | send order: 86.9% [73.3, 100.0] | 30 ms | yes | 13.1% [0.0, 26.7] n=61 | 86.9% [73.3, 100.0] | – | batch auction, w = 30 ms |
| [`DRpb…21hy`](https://solscan.io/account/DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy) | AgaveBam | batch auction, w = 40 ms: w within 40 ms–50 ms | 40 ms (40 ms–50 ms) | yes | 53.0% [19.7, 86.4] n=132 | 47.7% [14.1, 80.3] | 0.005 (kept) | batch auction, w = 40 ms |
| [`Fc6N…ULGD`](https://solscan.io/account/Fc6NNdS2j3EmrWbU6Uqt6wsKB5ef72NjaWfNxKYbULGD) | AgaveBam | scheduler passes, w = 5 ms: w within 5 ms–20 ms | 5 ms (5 ms–20 ms) | yes | 52.3% [0.0, 92.4] n=130 | 48.1% [7.7, 100.0] | 0.005 (kept) | priority, ties newest first |
| [`C8Be…JP1k`](https://solscan.io/account/C8Bey3LKVJHVqN6xPTeW8WJfUgFQAeGNBpT4Rp99JP1k) | JitoLabs | batch auction, w = 75 ms | 75 ms | yes | 9.2% [1.6, 23.5] n=358 | 90.8% [76.5, 98.4] | 0.005 (kept) | batch auction, w = 5 ms |
| [`E1r4…dxHL`](https://solscan.io/account/E1r4Psq84tHfQ6aPTvvDka4U3u8zPVD7gEUrH25RdxHL) | JitoLabs | send order: 100.0% [100.0, 100.0] | 5 ms (5 ms–10 ms) | no | 28.1% [7.7, 47.5] n=631 | 71.9% [53.4, 92.4] | – | batch auction, w = 5 ms |
| [`HEL1…e2TU`](https://solscan.io/account/HEL1USMZKAL2odpNBj2oCjffnFGaYwmbGmyewGv1e2TU) | JitoLabs | send order: 98.8% [96.4, 100.0] | 5 ms (5 ms–10 ms) | no | 18.5% [0.8, 37.1] n=471 | 81.5% [63.6, 99.1] | – | batch auction, w = 10 ms |
| [`Btsm…DAaH`](https://solscan.io/account/BtsmiEEvnSuUnKxqXj2PZRYpPJAc7C34mGz8gtJ1DAaH) | HarmonicAgave | send order: 86.0% [84.8, 88.2] | 40 ms | yes | 38.6% [14.0, 63.9] n=197 | 61.4% [36.1, 86.0] | 0.055 (fell back to arrival order (FIFO)) | batch auction, w = 50 ms |
| [`CAo1…Sve4`](https://solscan.io/account/CAo1dCGYrB6NhHh5xb1cGjUiu86iyCfMTENxgHumSve4) | HarmonicAgave | send order: 91.3% [86.7, 93.9] | 5 ms (5 ms–10 ms) | no | 46.4% [28.4, 60.5] n=603 | 53.6% [39.0, 72.9] | – | priority, ties oldest first |
| [`CW9C…cd1A`](https://solscan.io/account/CW9C7HBwAMgqNdXkNgFg9Ujr3edR2Ab9ymEuQnVacd1A) | HarmonicAgave | none detectable: p = 0.145, detectable from 98.0% | 100 ms (100 ms–200 ms) | no | 90.0% [90.0, 90.0] n=10 | 10.0% [10.0, 10.0] | – | none detectable; closest priority, ties oldest first |
| [`Ekvd…6C1f`](https://solscan.io/account/EkvdKhULbMFqjKBKotAzGi3kwMvMpYNDKJXXQQmi6C1f) | HarmonicAgave | batch auction, w = 40 ms | 40 ms | yes | 8.8% [8.7, 8.8] n=80 | 91.2% [91.2, 91.3] | 0.020 (kept) | batch auction, w = 40 ms |
| [`FBKF…jeNi`](https://solscan.io/account/FBKFWadXZJahGtFitAsBvbqh5968gLY7dMBBJUoUjeNi) | HarmonicAgave | send order: 85.5% [85.5, 85.5] | 40 ms (5 ms–40 ms) | yes | 14.5% [14.5, 14.5] n=55 | 85.5% [85.5, 85.5] | – | batch auction, w = 40 ms |
| [`Fd7b…69Nk`](https://solscan.io/account/Fd7btgySsrjuo25CJCj7oE7VPMyezDhnx7pZkj2v69Nk) | HarmonicAgave | send order: 89.6% [87.9, 93.3] | 5 ms (5 ms–20 ms) | yes | 10.5% [6.7, 12.1] n=162 | 89.5% [87.9, 93.3] | – | batch auction, w = 10 ms |
| [`Awes…vpLM`](https://solscan.io/account/Awes4Tr6TX8JDzEhCZY2QVNimT6iD1zWHzf1vNyGvpLM) | HarmonicFiredancer | send order: 86.0% [78.1, 92.0] | 20 ms (10 ms–20 ms) | yes | 49.0% [18.0, 73.7] n=457 | 51.0% [25.9, 83.8] | – | priority, ties newest first |
| [`Drif…xatz`](https://solscan.io/account/DrifTrN923QaouP89UxkQzFGbumKPCnfkNYQRwmZxatz) | HarmonicFiredancer | send order: 87.2% [84.6, 89.1] | 30 ms (5 ms–30 ms) | yes | 52.7% [10.1, 84.2] n=260 | 47.3% [16.8, 89.9] | – | priority, ties newest first |
| [`EvnR…qDo4`](https://solscan.io/account/EvnRmnMrd69kFdbLMxWkTn1icZ7DCceRhvmb2SJXqDo4) | HarmonicFiredancer | send order: 93.3% [90.0, 96.7] | 20 ms | no | 6.7% [3.3, 10.0] n=60 | 93.3% [90.0, 96.7] | – | batch auction, w = 20 ms |
| [`JUPi…1h4b`](https://solscan.io/account/JUPiTERrZqgf1jUyR7dSkhMx4Kn2qJyekWsg3LT1h4b) | HarmonicFiredancer | batch auction, w = 20 ms | 20 ms | yes | 63.0% [37.8, 82.9] n=548 | 37.0% [18.0, 61.2] | 0.005 (kept) | priority, ties oldest first |
| [`9eGr…8FoY`](https://solscan.io/account/9eGrDohdNTAo61DRHyfMuqKWXqYnA3i254Wiszxe8FoY) | Firedancer | send order: 91.4% [89.4, 93.2] | 30 ms | yes | 52.9% [26.7, 79.4] n=688 | 47.1% [21.2, 73.5] | 0.095 (fell back to arrival order (FIFO)) | priority, ties newest first |
| [`DNVZ…eWkf`](https://solscan.io/account/DNVZMSqeRH18Xa4MCTrb1MndNf3Npg4MEwqswo23eWkf) | Firedancer | send order: 100.0% [100.0, 100.0] | 5 ms | no | 27.2% [0.0, 71.2] n=243 | 72.8% [28.0, 100.0] | – | priority, ties newest first |
| [`JD54…HybB`](https://solscan.io/account/JD549HsbJHeEKKUrKgg4Fj2iyv2RGjsV7NTZjZUrHybB) | Firedancer | scheduler passes, w = 5 ms: w within 5 ms–10 ms, w not identifiable | 5 ms (5 ms–10 ms) | no | 20.8% [0.2, 47.8] n=456 | 79.2% [50.5, 99.8] | 0.010 (kept) | priority, ties newest first |
| [`9jxg…nGFP`](https://solscan.io/account/9jxgosAfHgHzwnxsHw4RAZYaLVokMbnYtmiZBreynGFP) | FireBAM | batch auction, w = 20 ms | 20 ms | yes | 41.2% [17.5, 66.7] n=461 | 59.2% [35.7, 82.0] | 0.005 (kept) | priority, ties newest first |
| [`LA1N…M3wc`](https://solscan.io/account/LA1NEzryoih6CQW3gwQqJQffK2mKgnXcjSQZSRpM3wc) | FireBAM | no data | – | – | 97.0% [97.0, 97.0] n=66 | 3.1% [3.1, 3.1] | – | priority, ties newest first |
| [`SSmB…oQLY`](https://solscan.io/account/SSmBEooM7RkmyuXxuKgAhTvhQZ36Z3G2WsmLGJKoQLY) | FireBAM | batch auction, w = 20 ms | 20 ms | yes | 6.1% [6.1, 6.1] n=66 | 93.9% [93.9, 93.9] | 0.005 (kept) | batch auction, w = 20 ms |
| [`qZMH…QrbP`](https://solscan.io/account/qZMH9GWnnBkx7aM1h98iKSv2Lz5N78nwNSocAxDQrbP) | FireBAM | none detectable: p = 0.087, detectable from 86.3% | 40 ms (40 ms–200 ms) | yes | 73.5% [73.5, 73.5] n=34 | 27.3% [27.3, 27.3] | – | priority, ties newest first |
| [`7PdK…ckFf`](https://solscan.io/account/7PdKhpKz7T39vZHFL1UfcYNDsLvay6hp4KPQq1aUckFf) | Rakurai | no data | – | – | 90.9% [90.9, 90.9] n=66 | 9.1% [9.1, 9.1] | – | priority, ties newest first |
| [`E3mD…4h3F`](https://solscan.io/account/E3mDbFMr8yZcCAZx5wPUFCRmLX31u8Nnj4jPoPc54h3F) | Rakurai | scheduler passes, w = 10 ms | 10 ms | yes | 15.1% [0.0, 43.5] n=146 | 84.9% [56.5, 100.0] | 0.005 (kept) | batch auction, w = 10 ms |
| [`ana2…xcfY`](https://solscan.io/account/ana2y2YvQ3ZPMwm6qhnN3nJoUSiT3qx5Pvetkq9xcfY) | Rakurai | send order: 96.8% [93.4, 99.8] | 5 ms | yes | 15.2% [2.3, 32.0] n=532 | 84.7% [68.3, 97.4] | – | batch auction, w = 5 ms |
| [`5Us1…5nBS`](https://solscan.io/account/5Us18hLZPXJTS4QVuGSsUw137Dyd2tgBaem24Xsf5nBS) | Agave | send order: 100.0% [100.0, 100.0] | 5 ms (5 ms–10 ms) | no | 24.1% [0.0, 62.7] n=212 | 75.9% [37.3, 100.0] | – | priority, ties oldest first |
| [`7QQG…wLpE`](https://solscan.io/account/7QQGNm3ptwinipDCyaCF7jY5katgmFUu1ieP2f7nwLpE) | Agave | send order: 83.7% [73.4, 98.5] | 5 ms (5 ms–10 ms) | yes | 16.9% [1.5, 27.5] n=248 | 83.7% [73.4, 98.3] | – | batch auction, w = 5 ms |

_Sweeps raise the fee with send time, so fee order and send order disagree on every pair; a batch length is identifiable when it orders at least 2% of pairs differently from both._
<!-- results:sweep:end -->

## 8. Did the probes land

<!-- results:inclusion:start -->
| Target family | Probes sent | Landed | Landed via QUIC | Landed via RPC | Landed in another leader's block | Landed inside the 4-slot window |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| AgaveBam | 944 | 87.9% [85.7, 89.9] | 87.2% [84.8, 89.2] | 100.0% [93.6, 100.0] | 3.5% [2.4, 5.0] | 95.1% |
| JitoLabs | 1,488 | 92.6% [91.2, 93.8] | 92.0% [90.5, 93.3] | 100.0% [96.7, 100.0] | 5.8% [4.7, 7.2] | 93.5% |
| HarmonicAgave | 1,064 | 99.2% [98.5, 99.6] | 99.2% [98.4, 99.6] | 100.0% [94.3, 100.0] | 9.7% [8.0, 11.6] | 88.8% |
| HarmonicFiredancer | 1,136 | 98.7% [97.8, 99.2] | 98.6% [97.7, 99.1] | 100.0% [94.9, 100.0] | 19.5% [17.3, 22.0] | 79.8% |
| Firedancer | 980 | 98.5% [97.5, 99.1] | 98.4% [97.3, 99.0] | 100.0% [93.6, 100.0] | 4.5% [3.3, 5.9] | 94.7% |
| FireBAM | 380 | 97.9% [95.9, 98.9] | 97.8% [95.6, 98.9] | 100.0% [86.2, 100.0] | 5.1% [3.3, 7.8] | 94.9% |
| Rakurai | 468 | 100.0% [99.2, 100.0] | 100.0% [99.1, 100.0] | 100.0% [86.2, 100.0] | 4.5% [3.0, 6.8] | 95.5% |
| Agave | 404 | 98.0% [96.1, 99.0] | 97.8% [95.6, 98.9] | 100.0% [92.6, 100.0] | 9.3% [6.9, 12.6] | 90.7% |
| all | 6,864 | 95.9% [95.5, 96.4] | – | – | – | – |

_Grouped by the family of the leader we targeted; 95% Wilson intervals._
<!-- results:inclusion:end -->

## 9. Naming the orderer from our probes alone

A classifier that sees only our probes' order, timing and landing names the component that
ordered each block; the same classifier on whole-block statistics (what an observer sees without
probes) is the baseline.

<!-- results:fingerprint:start -->
| Classifier | Our probes | Balanced | Whole-block baseline | Balanced | Chance (majority / uniform) | Rows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| per validator | 64.8% | 60.2% | 63.4% | 46.9% | 38.0% / 25.0% | 71 |
| per window | 53.8% | 44.6% | 35.8% | 33.1% | 34.1% / 25.0% | 772 |

| True orderer \ predicted | BAM node | Firedancer pack | Harmonic builder | local scheduler |
| --- | ---: | ---: | ---: | ---: |
| BAM node | 16 | 1 | 7 | 0 |
| Firedancer pack | 1 | 2 | 0 | 1 |
| Harmonic builder | 5 | 0 | 20 | 2 |
| local scheduler | 3 | 1 | 4 | 8 |

_Leave-one-validator-out: no validator is scored by a model that saw it. Confusion counts validators._
<!-- results:fingerprint:end -->

Per validator, our probes do not name the orderer much better than a view of whole blocks: once
many blocks of one validator are pooled, the client families leave clear traces in how all
transactions are arranged (entry sizes, the share of fee-ordered neighbours). Per single window
the probes in it do better than that window's own block statistics, which on one block are close
to chance. The rule cards above, not this classifier, are the study's answer to "by what rule".

## 10. Noise: controls and power

<!-- results:controls:start -->
| Control | Statistic | Expected | Observed |
| --- | --- | --- | ---: |
| Shuffled positions | validators selecting no rule | ≈ 95% | 98.1% |
| Shuffled positions | families selecting no rule | ≈ 95% | 100.0% |
| Shuffled positions | mean τ, priority vs position (ladder, format) | ≈ 0 | 0.000 [-0.045, 0.043] |
| Shuffled positions | sign test fires (ladder, format) | ≈ 5% | 4.0% |
| Shuffled positions | mean τ, send time vs position (tie, sweep) | ≈ 0 | 0.000 [-0.033, 0.036] |
| Inert variable (idx parity) | odd idx first | ≈ 50% | 49.4% [47.7, 51.2] n=8,894, p = 0.527 |
| Inert variable (idx parity) | mean τ | ≈ 0 | -0.025 [-0.062, 0.012], sign test p = 0.245 |

| Family | Blocks | Spearman priority vs index | Adjacent pairs falling in priority | Zero-priority share | Mean tx per block |
| --- | ---: | ---: | ---: | ---: | ---: |
| AgaveBam | 189 | -0.255 [-0.288, -0.224] | 57.6% | 23.9% | 525 |
| JitoLabs | 247 | -0.102 [-0.116, -0.087] | 54.8% | 23.5% | 475 |
| HarmonicAgave | 209 | -0.107 [-0.127, -0.088] | 53.9% | 27.0% | 543 |
| HarmonicFiredancer | 154 | -0.078 [-0.102, -0.054] | 57.3% | 27.0% | 572 |
| Firedancer | 124 | -0.256 [-0.295, -0.217] | 60.3% | 30.3% | 657 |
| FireBAM | 47 | -0.224 [-0.287, -0.165] | 60.1% | 19.5% | 540 |
| Rakurai | 56 | -0.168 [-0.211, -0.123] | 57.9% | 29.4% | 499 |
| Agave | 59 | -0.139 [-0.180, -0.097] | 54.2% | 21.3% | 517 |
| Unknown | 17 | -0.104 [-0.196, -0.029] | 55.3% | 24.8% | 637 |

_What an observer of whole blocks sees without our probes (supplementary; negative Spearman = higher priority earlier)._
<!-- results:controls:end -->

<!-- results:power:start -->
| Family | Reference rule | Pairs | Batch-blocks | Smallest detectable θ | ≈ concordance | Note |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| AgaveBam | priority, ties newest first | 3,076 | 142 | 0.10 | 56.1% | – |
| JitoLabs | batch auction, w = 10 ms | 4,693 | 204 | 0.10 | 56.1% | – |
| HarmonicAgave | batch auction, w = 10 ms | 4,146 | 180 | 0.10 | 56.1% | – |
| HarmonicFiredancer | batch auction, w = 10 ms | 3,676 | 140 | 0.10 | 56.2% | – |
| Firedancer | priority, ties newest first | 3,558 | 118 | 0.10 | 56.4% | – |
| FireBAM | priority, ties newest first | 1,384 | 43 | 0.15 | 59.7% | – |
| Rakurai | batch auction, w = 10 ms | 1,759 | 55 | 0.15 | 59.6% | – |
| Agave | priority, ties oldest first | 1,336 | 50 | 0.15 | 59.2% | – |
| Unknown | arrival order (FIFO) | 20 | 11 | 1.50 | 85.9% | – |

| Validator | Reference rule | Pairs | Batch-blocks | Smallest detectable θ | ≈ concordance | Note |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| [`2GUn…1rSx`](https://solscan.io/account/2GUnfxZavKoPfS9s3VSEjaWDzB3vNf5RojUhprCS1rSx) | arrival order (FIFO) | 3 | 1 | – | – | power stays below 80% up to theta 5 |
| [`5Cch…q9Ux`](https://solscan.io/account/5Cchr1XGEg7dbBXByV5NY2ad8jfxAM7HA3x8D56rq9Ux) | priority, ties newest first | 975 | 33 | 0.15 | 59.4% | – |
| [`5EhG…ULeC`](https://solscan.io/account/5EhGYUyQNrxgUbuYF4vbL2SZDT6RMfhq3yjeyevvULeC) | batch auction, w = 5 ms | 773 | 28 | 0.20 | 62.3% | – |
| [`5d9M…UWwG`](https://solscan.io/account/5d9Mdc2Zk8as8GL1AxQeXxv5htBBvC5bjfmsXC7UUWwG) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`5ess…4dRn`](https://solscan.io/account/5essVpBYvocZkQnkrWsMDfRPwtp4BeihJRaweKLR4dRn) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`6y7V…syrK`](https://solscan.io/account/6y7V8dL673XFzm9QyC5vvh3itWkp7wztahBd2yDqsyrK) | arrival order (FIFO) | 3 | 1 | – | – | power stays below 80% up to theta 5 |
| [`7CR3…AqCT`](https://solscan.io/account/7CR3Jq4ny2tsr3DX3DvyjoU8TYs776MGkU6nLMWjAqCT) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`9Xhm…7ct7`](https://solscan.io/account/9XhmwxwWtoR6oD4xpSHutoTXku8hXyq76XoTKFZb7ct7) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`9rkJ…NDSQ`](https://solscan.io/account/9rkJMARqK6VBkcxGfKBAwnA44gPAfGxPbPsfsggFNDSQ) | batch auction, w = 30 ms | 203 | 11 | 0.50 | 73.9% | – |
| [`Aw5w…k1ex`](https://solscan.io/account/Aw5wEMXhbygFLR7jHtHpih8QvxVBGAMTqsQ2SjWPk1ex) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`BJvr…mTca`](https://solscan.io/account/BJvrWSfonXnS2Km8iA9KLY6D6vS3GcsaUwUNPFBumTca) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`BUv4…RyyB`](https://solscan.io/account/BUv44cVtsdvU9z2BfFGk6s5JZZWrmVnq5qCaii5ARyyB) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`Cu9L…Rq86`](https://solscan.io/account/Cu9Ls6dsTL6cxFHZdStHwVSh1uy2ynXz8qPJMS5FRq86) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`DRpb…21hy`](https://solscan.io/account/DRpbCBMxVnDK7maPM5tGv6MvB3v1sRMC86PZ8okm21hy) | batch auction, w = 40 ms | 311 | 10 | 0.30 | 68.4% | – |
| [`Fc6N…ULGD`](https://solscan.io/account/Fc6NNdS2j3EmrWbU6Uqt6wsKB5ef72NjaWfNxKYbULGD) | priority, ties newest first | 776 | 30 | 0.20 | 61.5% | – |
| [`Fyrw…6d4j`](https://solscan.io/account/FyrwfMaomErzqrFUXMjCJ7mA4u81DsiDdrzC3MJD6d4j) | arrival order (FIFO) | 5 | 3 | – | – | power stays below 80% up to theta 5 |
| [`GBQ2…AAAA`](https://solscan.io/account/GBQ2GvTzmjXMu97dr7WUnLKYTNim1aoZsYS53KYXAAAA) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`GEM1…UReR`](https://solscan.io/account/GEM1N1UE3C8BB8EaaEPrcFvT3iLMVrurknjW5AYjUReR) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`Ninj…rmjs`](https://solscan.io/account/Ninja1spj6n9t5hVYgF3PdnYz2PLnkt7rvaw3firmjs) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`PAWs…YAZ6`](https://solscan.io/account/PAWsME7oYbjt5TRNc11mBa33JhKnQr9AYherdr9YAZ6) | arrival order (FIFO) | 3 | 3 | – | – | power stays below 80% up to theta 5 |
| [`bkpk…iR8Z`](https://solscan.io/account/bkpk9KVsDRfrArzzmkJ9mPEvbXfQxczzQYR3QMGiR8Z) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`ciTy…6cQj`](https://solscan.io/account/ciTyjzN9iyobidMycjyqRRM7vXAHXkFzH3m8vEr6cQj) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`kyzz…1xgj`](https://solscan.io/account/kyzzzgRymGpePUsLyr48kQHt53kh5CSfRH1qfvz1xgj) | arrival order (FIFO) | 4 | 2 | – | – | power stays below 80% up to theta 5 |
| [`5pPR…HzSm`](https://solscan.io/account/5pPRHniefFjkiaArbGX3Y8NUysJmQ9tMZg3FrFGwHzSm) | arrival order (FIFO) | 5 | 3 | – | – | power stays below 80% up to theta 5 |
| [`8gDf…2LoM`](https://solscan.io/account/8gDfqHXZ5ektqQQrLrCGtSzYRwkWQrm3ANnAvQCt2LoM) | arrival order (FIFO) | 3 | 3 | – | – | power stays below 80% up to theta 5 |
| [`A9mv…8FAC`](https://solscan.io/account/A9mvukTd77EbRoBX4ydSCFQHdu5bsRFkNXTTRstA8FAC) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`ABC1…thwY`](https://solscan.io/account/ABC1U4cf9DZMwqy8ktEr4WJj8VHmVBQibbC57gEJthwY) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`C8Be…JP1k`](https://solscan.io/account/C8Bey3LKVJHVqN6xPTeW8WJfUgFQAeGNBpT4Rp99JP1k) | batch auction, w = 5 ms | 1,080 | 37 | 0.15 | 59.2% | – |
| [`E1r4…dxHL`](https://solscan.io/account/E1r4Psq84tHfQ6aPTvvDka4U3u8zPVD7gEUrH25RdxHL) | batch auction, w = 5 ms | 1,806 | 70 | 0.15 | 59.3% | – |
| [`EUcJ…XctB`](https://solscan.io/account/EUcJwf7jXskRE6NZBtFPVH2EedNvNYko8LL2WT62XctB) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`FbYX…uT4k`](https://solscan.io/account/FbYX2uN573G5WsgiPdHU6fS5PNUyjdXfGfpZNkYUuT4k) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`Gv9g…MsWV`](https://solscan.io/account/Gv9gguvrAkgQtB5g5a3Un7trcHCxLYsk8vSojLmQMsWV) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`HEL1…e2TU`](https://solscan.io/account/HEL1USMZKAL2odpNBj2oCjffnFGaYwmbGmyewGv1e2TU) | batch auction, w = 10 ms | 1,757 | 73 | 0.15 | 58.8% | – |
| [`Ha1i…qJJU`](https://solscan.io/account/Ha1iade1AH3B12K9SccfWoPdFtQKKQsj2ZyWwxcjqJJU) | arrival order (FIFO) | 3 | 1 | – | – | power stays below 80% up to theta 5 |
| [`LeDb…uE3V`](https://solscan.io/account/LeDbQ99QT342j9S5YdyXLrsq2Gu3T3dMGajExdAuE3V) | arrival order (FIFO) | 3 | 1 | – | – | power stays below 80% up to theta 5 |
| [`UPSC…GubF`](https://solscan.io/account/UPSCQNqdbiaqrQou9X9y8mr43ZHzvoNpKC26Mo7GubF) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`vu1s…TC2D`](https://solscan.io/account/vu1sGn2f1Xim6voHNLt4nLn38zNkYdLasU7hEr1TC2D) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`2m1A…oAAb`](https://solscan.io/account/2m1A2WM1vte7RWz5xTTw4i1SiXmngVtXhqFERaUjoAAb) | arrival order (FIFO) | 6 | 6 | 3.00 | 95.3% | – |
| [`3ytg…9gpJ`](https://solscan.io/account/3ytgyJXpm3teGLwkHqp9h2qqYNpY4Z1QpWAeVpz79gpJ) | arrival order (FIFO) | 6 | 2 | 3.00 | 96.7% | – |
| [`4k6w…5zSF`](https://solscan.io/account/4k6wgP5WPBKQpsFGtzuXNrjcTE2fKWLj17nDvFeG5zSF) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`5Zqv…q9qk`](https://solscan.io/account/5ZqveVffQPiUbkjBg4KD9kib1MKHLqiFno4ke9jSq9qk) | arrival order (FIFO) | 59 | 5 | 0.75 | 81.8% | – |
| [`9UM8…Jd9i`](https://solscan.io/account/9UM8wQ8F5oMiRcP5YdqD6Lr4krpBWCD8LtgQYoisJd9i) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`9W3Q…Wws7`](https://solscan.io/account/9W3QTgBhkU4Bwg6cwnDJo6eGZ9BtZafSdu1Lo9JmWws7) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`9iFP…nz8R`](https://solscan.io/account/9iFPQbP1jGkj67sXg6YLLGRUBVEDMcapdS6jmCZSnz8R) | arrival order (FIFO) | 30 | 3 | 1.00 | 86.6% | – |
| [`A1vq…ANy4`](https://solscan.io/account/A1vqhA2fS6K7CvHsJKX1ACcHJFEmyRg4KuR5pctHANy4) | priority, ties oldest first | 29 | 2 | 1.00 | 87.1% | – |
| [`AMuk…6DJF`](https://solscan.io/account/AMukCLCr52XxsEjXoDxKKxjNg4FpnsReXNaQx8aR6DJF) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`Btsm…DAaH`](https://solscan.io/account/BtsmiEEvnSuUnKxqXj2PZRYpPJAc7C34mGz8gtJ1DAaH) | batch auction, w = 50 ms | 854 | 31 | 0.20 | 61.8% | – |
| [`CAo1…Sve4`](https://solscan.io/account/CAo1dCGYrB6NhHh5xb1cGjUiu86iyCfMTENxgHumSve4) | priority, ties oldest first | 1,643 | 54 | 0.15 | 59.5% | – |
| [`CW9C…cd1A`](https://solscan.io/account/CW9C7HBwAMgqNdXkNgFg9Ujr3edR2Ab9ymEuQnVacd1A) | priority, ties oldest first | 10 | 1 | 3.00 | 98.0% | – |
| [`Ekvd…6C1f`](https://solscan.io/account/EkvdKhULbMFqjKBKotAzGi3kwMvMpYNDKJXXQQmi6C1f) | batch auction, w = 40 ms | 673 | 30 | 0.20 | 61.4% | – |
| [`Ex1A…XAaT`](https://solscan.io/account/Ex1AxFCipXGfSxgvXPPT3nPQUARddCduHwKR6jHiXAaT) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`FBKF…jeNi`](https://solscan.io/account/FBKFWadXZJahGtFitAsBvbqh5968gLY7dMBBJUoUjeNi) | batch auction, w = 40 ms | 207 | 9 | 0.50 | 75.7% | – |
| [`Fd7b…69Nk`](https://solscan.io/account/Fd7btgySsrjuo25CJCj7oE7VPMyezDhnx7pZkj2v69Nk) | batch auction, w = 10 ms | 533 | 22 | 0.30 | 67.3% | – |
| [`G3a3…KkBQ`](https://solscan.io/account/G3a3iYZKNLbivothF3twqcaTCEvoPb9uJ2bFc9DNKkBQ) | priority, ties oldest first | 28 | 1 | 1.00 | 87.6% | – |
| [`Stak…itLh`](https://solscan.io/account/Stakex4B2tpDHPWGvV1dninfiaYCGdakgTknpzPitLh) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`shft…Vikj`](https://solscan.io/account/shftkxnsXmqAkmLgz9Mn7bNB5Fr6mKgFc58kFHfVikj) | priority, ties oldest first | 57 | 3 | 0.75 | 82.3% | – |
| [`6ptu…JwWG`](https://solscan.io/account/6ptuwW4rg5A3wP9hNKxeymG3QhSagugPd3grnMTGJwWG) | priority, ties newest first | 56 | 2 | 0.75 | 82.6% | – |
| [`97jb…eeMt`](https://solscan.io/account/97jbhVBYcSmwGXjrx5PPWXucDsVBqwyoQ6rzP3B6eeMt) | priority, ties oldest first | 56 | 2 | 0.75 | 82.6% | – |
| [`Awes…vpLM`](https://solscan.io/account/Awes4Tr6TX8JDzEhCZY2QVNimT6iD1zWHzf1vNyGvpLM) | priority, ties newest first | 1,234 | 48 | 0.15 | 59.0% | – |
| [`CQdL…ow12`](https://solscan.io/account/CQdLgwkmmHF8DE6t4sguXMTXWDxq3oLh3xfekDTmow12) | priority, ties oldest first | 33 | 4 | 1.00 | 85.7% | – |
| [`Drif…xatz`](https://solscan.io/account/DrifTrN923QaouP89UxkQzFGbumKPCnfkNYQRwmZxatz) | priority, ties newest first | 569 | 19 | 0.20 | 62.5% | – |
| [`EJsJ…kJk8`](https://solscan.io/account/EJsJx7Smt3jBheNF2ReTxq3HXh9Aczt6PHK12B5tkJk8) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`EvnR…qDo4`](https://solscan.io/account/EvnRmnMrd69kFdbLMxWkTn1icZ7DCceRhvmb2SJXqDo4) | batch auction, w = 20 ms | 198 | 14 | 0.50 | 73.0% | – |
| [`GZ4U…vp5H`](https://solscan.io/account/GZ4U21pYmFkCk6ZNTHRzfVEZZXauyZLKDmuuuKGwvp5H) | arrival order (FIFO) | 28 | 1 | 1.00 | 87.6% | – |
| [`HnrX…xTYA`](https://solscan.io/account/HnrXmm65CyvmSyvwEMjmyHzYjAyhHiHEJ7s1pf6BxTYA) | priority, ties oldest first | 28 | 1 | 1.00 | 87.6% | – |
| [`JUPi…1h4b`](https://solscan.io/account/JUPiTERrZqgf1jUyR7dSkhMx4Kn2qJyekWsg3LT1h4b) | priority, ties oldest first | 1,470 | 52 | 0.15 | 59.4% | – |
| [`pSo1…nkfc`](https://solscan.io/account/pSo1KZXgG1EGHch6pPmscfYhv3buXyaj2dR8WkVnkfc) | priority, ties oldest first | 28 | 1 | 1.00 | 87.6% | – |
| [`9eGr…8FoY`](https://solscan.io/account/9eGrDohdNTAo61DRHyfMuqKWXqYnA3i254Wiszxe8FoY) | priority, ties newest first | 1,677 | 50 | 0.15 | 59.8% | – |
| [`BFmq…Wmzy`](https://solscan.io/account/BFmqBsaGjeYA7gHpReKmnVkRyhqiMmHoR9ttv1crWmzy) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`DNVZ…eWkf`](https://solscan.io/account/DNVZMSqeRH18Xa4MCTrb1MndNf3Npg4MEwqswo23eWkf) | priority, ties newest first | 664 | 24 | 0.20 | 62.1% | – |
| [`JD54…HybB`](https://solscan.io/account/JD549HsbJHeEKKUrKgg4Fj2iyv2RGjsV7NTZjZUrHybB) | priority, ties newest first | 1,215 | 42 | 0.15 | 59.3% | – |
| [`9jxg…nGFP`](https://solscan.io/account/9jxgosAfHgHzwnxsHw4RAZYaLVokMbnYtmiZBreynGFP) | priority, ties newest first | 1,050 | 33 | 0.15 | 59.7% | – |
| [`LA1N…M3wc`](https://solscan.io/account/LA1NEzryoih6CQW3gwQqJQffK2mKgnXcjSQZSRpM3wc) | priority, ties newest first | 178 | 5 | 0.50 | 77.0% | – |
| [`SSmB…oQLY`](https://solscan.io/account/SSmBEooM7RkmyuXxuKgAhTvhQZ36Z3G2WsmLGJKoQLY) | batch auction, w = 20 ms | 94 | 2 | 0.50 | 78.8% | – |
| [`qZMH…QrbP`](https://solscan.io/account/qZMH9GWnnBkx7aM1h98iKSv2Lz5N78nwNSocAxDQrbP) | priority, ties newest first | 62 | 3 | 0.75 | 81.7% | – |
| [`7PdK…ckFf`](https://solscan.io/account/7PdKhpKz7T39vZHFL1UfcYNDsLvay6hp4KPQq1aUckFf) | priority, ties newest first | 178 | 5 | 0.50 | 77.0% | – |
| [`E3mD…4h3F`](https://solscan.io/account/E3mDbFMr8yZcCAZx5wPUFCRmLX31u8Nnj4jPoPc54h3F) | batch auction, w = 10 ms | 539 | 20 | 0.20 | 61.9% | – |
| [`ana2…xcfY`](https://solscan.io/account/ana2y2YvQ3ZPMwm6qhnN3nJoUSiT3qx5Pvetkq9xcfY) | batch auction, w = 5 ms | 1,042 | 30 | 0.15 | 59.9% | – |
| [`5Us1…5nBS`](https://solscan.io/account/5Us18hLZPXJTS4QVuGSsUw137Dyd2tgBaem24Xsf5nBS) | priority, ties oldest first | 634 | 23 | 0.20 | 62.2% | – |
| [`7QQG…wLpE`](https://solscan.io/account/7QQGNm3ptwinipDCyaCF7jY5katgmFUu1ieP2f7nwLpE) | batch auction, w = 5 ms | 672 | 24 | 0.20 | 62.1% | – |
| [`9hQq…wnTf`](https://solscan.io/account/9hQqNe3DQTiwhspatewA8EXhz12e6sq5UJVJ2qNRwnTf) | arrival order (FIFO) | 30 | 3 | 1.00 | 86.6% | – |
| [`2FL6…Ypef`](https://solscan.io/account/2FL6757bjpBe1datbq91eKDCp7Jqpce5HC8aZ6eTYpef) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`6aow…VGPs`](https://solscan.io/account/6aow5rTURdbhbeMDrFrbP2GR5vZjMEhktEy87iH1VGPs) | arrival order (FIFO) | 2 | 2 | – | – | power stays below 80% up to theta 5 |
| [`7KVn…jQPN`](https://solscan.io/account/7KVnUL45FH4N8Z7x1dnvJ7GzMx12cs81mS26mHzNjQPN) | arrival order (FIFO) | 3 | 3 | – | – | power stays below 80% up to theta 5 |
| [`ANCV…6jch`](https://solscan.io/account/ANCVpxEySGWWLqqkVKw2xWYDE9UP4fmZWMCBr5t96jch) | arrival order (FIFO) | 11 | 2 | 2.00 | 93.6% | – |
| [`DbWT…CuEr`](https://solscan.io/account/DbWTEseEDkBQzxbUFaTm9JsLy9FfeEGm1bsrfHSzCuEr) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |
| [`G83j…2Qys`](https://solscan.io/account/G83jy4NB16A4mchfRjZvfH8SPH2TxwjSZYxh5fzg2Qys) | arrival order (FIFO) | 1 | 1 | – | – | power stays below 80% up to theta 5 |

_Smallest Mallows θ the rule-vs-random test detects with 80% power at α = 0.05, from simulations at the unit's own sizes._
<!-- results:power:end -->

## 11. Timing of leader windows after Alpenglow

<!-- results:clock:start -->
| Slots with an exact time | Window period p10 / p50 / p90 (ms) | Slot 2 of the window starts after (median ms) | Slot 3 of the window starts after (median ms) | Slot 4 of the window starts after (median ms) |
| ---: | ---: | ---: | ---: | ---: |
| 56,489 | 790 / 819 / 971 (n=14,089) | 149 (n=14,119) | 348 (n=14,112) | 548 (n=14,106) |

| Send anchor | Batches | Planned first send (ms) | Actual first send p10 / p50 / p90 (ms) |
| --- | ---: | ---: | ---: |
| before the window, 3 slots | 900 | -622 | -2976 / -919 / -330 |
| in the window, +1 slot | 239 | +207 | -2166 / +4 / +420 |
| in the window, +2 slots | 110 | +413 | -2154 / +21 / +758 |

_Times are the node's own first-shred timestamps; windows are the 4-slot groups starting at slots divisible by 4; send phase is the first send minus the exact start of the batch's window (negative = before the window)._
<!-- results:clock:end -->

After Alpenglow a leader window is not four equal slots: inside a window slots start at fixed
offsets of about 150, 350 and 550 ms, and the boundary between windows waits for the previous
window's certificate, so window starts drift like a random walk around ~820 ms. The second table is
why every comparison in this report uses measured send times and not the intended anchor: in runs
2–5 the predicted window start, made minutes ahead, could be off by seconds.


## 12. Replication on a second day

The same harness, design and analysis, run again about a day after the main measurement, to see
whether the rules hold or move (an external builder or sequencer can change its rule without any
client upgrade).

<!-- results:replication:start -->
| Family | Day (UTC) | Queued together: higher fee first | Equal fees: older first | Over time: earlier sent first |
| --- | --- | ---: | ---: | ---: |
| AgaveBam | 2026-11-01 | 92.0% [89.4, 94.2] n=1,432 | 43.5% [31.5, 55.1] n=363 | 49.4% [23.0, 81.4] n=340 |
| AgaveBam | 2026-11-02 | 92.2% [86.3, 96.9] n=232 | 22.6% [10.7, 39.3] n=84 | 60.2% [26.0, 91.8] n=191 |
| JitoLabs | 2026-11-01 | 70.3% [63.0, 77.0] n=2,152 | 66.2% [58.9, 73.1] n=764 | 93.9% [84.8, 100.0] n=477 |
| JitoLabs | 2026-11-02 | 85.4% [72.5, 96.5] n=233 | 75.0% [57.1, 100.0] n=84 | 100.0% [100.0, 100.0] n=174 |
| HarmonicAgave | 2026-11-01 | 69.4% [65.2, 73.8] n=1,990 | 50.0% [45.5, 54.2] n=650 | 87.9% [82.6, 91.6] n=397 |
| HarmonicAgave | 2026-11-02 | 73.5% [63.5, 83.4] n=287 | 58.9% [46.4, 76.8] n=112 | 88.1% [85.5, 93.3] n=151 |
| HarmonicFiredancer | 2026-11-01 | 89.2% [84.2, 93.3] n=1,505 | 50.2% [44.3, 56.2] n=616 | 88.0% [82.1, 92.4] n=401 |
| HarmonicFiredancer | 2026-11-02 | 93.6% [84.5, 97.8] n=202 | 60.7% [60.7, 60.7] n=28 | 88.7% [78.8, 95.7] n=133 |
| Firedancer | 2026-11-01 | 94.9% [90.3, 98.5] n=1,328 | 8.3% [5.0, 12.5] n=504 | 97.0% [94.8, 99.1] n=627 |
| Firedancer | 2026-11-02 | 96.5% [89.9, 100.0] n=227 | 23.2% [19.6, 27.7] n=112 | 93.0% [89.4, 100.0] n=100 |
| FireBAM | 2026-11-01 | 92.9% [89.5, 96.0] n=393 | 26.4% [7.1, 46.4] n=140 | 64.4% [39.9, 83.6] n=404 |
| FireBAM | 2026-11-02 | 97.9% [93.6, 100.0] n=140 | 17.9% [0.0, 32.1] n=84 | 94.9% [94.9, 94.9] n=39 |
| Rakurai | 2026-11-01 | 76.6% [66.2, 86.4] n=538 | 39.0% [29.8, 49.6] n=251 | 90.5% [78.5, 98.4] n=348 |
| Rakurai | 2026-11-02 | 78.1% [64.3, 91.7] n=169 | 55.4% [53.6, 57.1] n=56 | 99.0% [97.0, 100.0] n=198 |
| Agave | 2026-11-01 | 81.5% [71.9, 89.9] n=428 | 56.0% [44.6, 67.9] n=168 | 84.7% [73.5, 98.7] n=261 |
| Agave | 2026-11-02 | 82.9% [72.2, 93.7] n=252 | 44.4% [44.4, 44.4] n=27 | 100.0% [100.0, 100.0] n=131 |

_Day = UTC date on which a run started. Rates are shares of pairs of our probes in one block, 95% interval over batches._
<!-- results:replication:end -->

The second day was a separate run on the next evening, with the corrected harness. For probes
queued together, the day-2 interval overlaps day 1's for every family (the largest move is
JitoLabs, 70% to 85%): the rules held. Over time, the local schedulers, Firedancer and both Harmonic clients kept arrival
order, and the BAM families kept their mixed, batch-shaped result. Equal-fee results move more
between days; their intervals are wide and only Firedancer's newest-first rule is strong on both
days.

## 13. What went wrong

<!-- results:what_went_wrong_stats:start -->
| What | Count | Detail |
| --- | ---: | --- |
| Batches skipped | 98 | 10 connect, 16 error, 15 late, 57 late_at_send |
| Probes with a send error | 404 | 372 “closed by peer: dropped (code 1)”, 30 “sendTransaction: gave up after retries”, 2 “reconnect failed: quic handshake timed out” |
| Probes that landed with a transaction error | 0 | – |
| Probes signed with an expired blockhash | 3,712 | 196 of them landed. In runs 2–5 a batch was prepared on a window-start prediction made minutes ahead, signed too early and sent with a blockhash older than 140 slots, so whole batches could not land; fixed by re-predicting while waiting. Excluded from the landing statistics; their landed probes still count for order |
| Landed probes in another leader's block (spills) | 550 | 8.4% of 6,586 attributed probes; attributed to the block's producer |
| Landed before / after the targeted window | 513 / 91 | – |
| Landed probes without a stored block | 0 | – |
| Client changed during the run | 0 windows | 0 probes, 0 validators; each label counted in its own family |
| Input issues logged by the loader | 0 | – |
<!-- results:what_went_wrong_stats:end -->

In the order we hit them:

1. **The public WebSocket is not a clock.** `slotsUpdatesSubscribe` on the public endpoint delivered
   first-shred events 0.5 s late at the median and up to 8 s late (p90). The events carry the node's
   own timestamp, which is exact; we use those, and the entry stream's first-shred events, never
   the delivery time.
2. **Idle unstaked QUIC connections are pruned.** In run 2 we opened a connection 2.5 s before each
   batch; servers closed many of them (`closed by peer: dropped`) before the first stream, and whole
   batches were lost, most on BAM nodes, which front dozens of validators behind one address. From
   run 3 on, one connection per TPU address is shared by all batches and replaced the moment the
   server closes it.
3. **Batches signed too early.** In runs 2–5 every batch task slept until a preparation time computed
   from a window-start prediction made when the run began, up to 30 minutes ahead. Window starts
   drift, so a batch could be signed tens of seconds before its window and sent with an expired
   blockhash. The data showed it plainly: blockhash age at send grew linearly with time since the run
   started while our slot clock stayed on time. We had first blamed the RPC endpoints and switched
   blockhash sources twice; the fix was to re-predict the window at least every 5 s while waiting.
   Run 6 then landed 99%, and the replication 100%. The same fault also made many day-1 sweeps start
   seconds before their window; a sweep queued that early is sorted as one burst, which at first
   looked like "Firedancer re-sorts across time". Every statement about order over time therefore
   uses only batches whose first send was inside the window, measured against first-shred times.
4. **Public RPC limits.** The public endpoint answered bursts with HTTP 429, and one free provider
   started answering 403 to our IP after a long collection; collection now runs on a second
   endpoint and tolerates a node's errors. Run 5's collection stopped on one node's error and was
   finished afterwards with `precedence collect` and `precedence reconcile`, which is why its end
   time is missing.
5. **Block footers are not always delivered** by the entry stream: some runs have them for most
   blocks, others for none. Nothing depends on them; they are shown as a cross-check of gossip.
6. **The RPC path lands late.** `sendTransaction` (with `maxRetries: 0`) forwards to the current and
   next leaders, so probes sent through it often land in a neighbour's block; they are attributed to
   the block's producer like every other probe.
7. **Unsolicited deposits.** Three transfers we did not send arrived in two of our fee payers
   (28,994 lamports in total, listed with signatures in `data/ledger/foreign_deposits.json`). With
   them, funding + deposits − fees settled in `data/ledger/spend.jsonl` equals the wallets' balance
   to the lamport.
8. **Our own predictions.** Three did not hold (section 3): Agave's ties at 0.5 ms are ordered after
   all (we expected noise), the typical Harmonic validator does not run 50 ms batches, and at "equal
   fee" Firedancer puts the v1 form first because the legacy form costs slightly more.

