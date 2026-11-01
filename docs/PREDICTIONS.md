# Predictions (written before the first live transaction)

This file was committed before any probe was sent to mainnet; its git timestamp precedes every
measurement in `data/`. Each prediction comes from reading the block producer's source code (or,
where the producer is closed, its operator documentation). The live data then confirms or refutes
each line, and [REPORT.md](../REPORT.md) says which.

Source references are to the tags running on mainnet on 1 November 2026:
Agave `v4.4.0` / `v4.3.2` (same scheduler), Jito-Solana `v4.4.0-jito`, Firedancer `v26.10.5`,
FireBAM `main@8c749ec`, Harmonic Salsa `master-harmonic@07ca775`, Samba `v26.10.5-harmonic@e40e2bd`.

Notation: *priority* is `(priority fee + 2500·signatures)·1e6 / (cost + 1)`
(`agave/core/src/transaction_priority.rs`); all probes of a batch have the same cost, so within
a batch priority order equals fee order.

## Who decides the order

| Family (gossip `clientId`) | Orders the block | Evidence |
|---|---|---|
| `Agave` | the validator's greedy scheduler | `agave/core/src/banking_stage.rs` (both block-production methods route to the greedy scheduler) |
| `JitoLabs` | the same greedy scheduler, plus bundles | `jito-solana/core/src/banking_stage/.../greedy_scheduler.rs` (regular txs skip accounts locked by bundles) |
| `Rakurai` | a closed scheduler library ("reward relative to estimated compute cost") | `rakurai-validator/README.md`, `rakurai_docs/transaction_inclusion/tips.md` |
| `Firedancer` | the pack tile | `firedancer/src/disco/pack/fd_pack.c` |
| `AgaveBam`, `FireBAM` | an external BAM node; the validator executes its sequence | `jito-solana/core/src/banking_stage/transaction_scheduler/scheduler_controller.rs` (local scheduling off while BAM is connected), `firebam/src/disco/pack/fd_pack_bam.h`, bam.dev docs |
| `HarmonicAgave`, `HarmonicFiredancer` | an external Harmonic builder with a per-validator strategy (`fba` default, `fifo`, `mrev`) | docs.harmonic.gg `scheduling-strategies`, `salsa/harmonic-scheduler/src/validator/block_stage.rs`, `samba/src/disco/pack/fd_pack_tile.c` |

**P0.** For BAM and Harmonic validators the gossip `tpuQuic` address is a proxy (a BAM node or a
Harmonic remote TPU), not the validator. Our direct QUIC sends to those validators therefore enter
the external orderer, and the rule we measure is that orderer's rule.

## The probes and what each family should show

`Ladder`: 8 probes, fee ladder independent of send order, burst. `Tie`: 8 probes, equal fee, sent
0.5 / 2 / 5 ms apart. `Sweep`: 12 probes 10 or 20 ms apart, fee rising with send time. `Format`:
4 v1 + 4 legacy at equal priority. Arm `distinct`: no write conflicts between probes; arm `shared`:
every probe also write-locks one shared account.

### Agave and JitoLabs (local greedy scheduler)

- **P-A1 Ladder, queued before the window (sent ~3 slots early): priority order.** The scheduler
  pops a max-heap ordered by priority, then lower `arrival_order` (`transaction_priority_id.rs`
  L19-25). Packets that arrive within 20 slots of the leader's slot are held, not dropped
  (`decision_maker.rs` L74-102).
- **P-A2 `shared` holds priority order more strictly than `distinct`.** Conflicting transactions
  are chained onto the one thread that holds the lock (`thread_aware_account_locks.rs` L89-107),
  so they execute in schedule order; non-conflicting ones are spread over 4 workers
  (`banking_stage.rs` L85) whose batches (≤64 txs, `consumer.rs` L35) become separate entries
  recorded in commit order.
- **P-A3 Tie: oldest first (FIFO) at 2 and 5 ms spacing; no reliable order at 0.5 ms.** Ties
  break on `arrival_order`, stamped after 8 parallel check workers (`banking_stage.rs` L518), so
  sends <1 ms apart can swap.
- **P-A4 Ladder arriving mid-window: weaker priority order than P-A1.** The controller receives
  and schedules in a continuous loop (`scheduler_controller.rs`); probes compared only within a
  pass.
- **P-A5 Sweep (10–20 ms apart): send order.** Each probe arrives in its own pass, so nothing
  competes; the rising fee is invisible.
- **P-A6 Inclusion does not depend on fee.** Blocks are ~25% of the 50M CU limit; the priority
  floor only applies when the buffer is ≥99% full (`scheduler_controller.rs` L40-47).

### Firedancer (pack)

- **P-F1 Ladder: strict priority order**, compared by exact cross-multiplication of rewards and
  compute (`fd_pack.c` L227).
- **P-F2 Tie: NEWEST first (LIFO) when the probes are queued together.** An equal key is inserted
  "epsilon larger" in the treap (`fd_treap.c` L254-260) and scheduling iterates from the maximum
  (`fd_pack.c` L2043). This is the opposite of Agave and the sharpest single difference we can test.
- **P-F3 Sweep: send order** (pack schedules continuously, one transaction per microblock,
  `fd_pack_tile.c` L65).
- **P-F4 Pre-window sends are kept** until the blockhash expires (`fd_pack_tile.c` L40-50).

### BAM (AgaveBam, FireBAM)

- **P-B1 Sweep: a batch auction.** The BAM node "processes [transactions] in 50ms batches,
  ordering by fee density" (bam.dev, 29 Apr 2026); the October changelog moves to 20 ms rounds at
  200 ms slots. Prediction: the best model is `batched(w)` with w between 20 and 50 ms; within a
  batch the later, higher-fee probe comes first, across batches send order holds.
- **P-B2 Ladder burst: priority order** (one batch).
- **P-B3 Pre-window sends can land in the previous BAM leader's block** (the BAM node serves
  whichever connected validator is leader). Attribution follows the block's producer.
- **P-B4 FireBAM behaves like AgaveBam on order** (same BAM node; FIFO execution of its batches,
  `fd_pack_bam.h` L75-100).

### Harmonic (HarmonicAgave, HarmonicFiredancer)

- **P-H1 Validators on the default `fba` strategy: `batched(w≈50 ms)`** ("double-buffered 50 ms
  windows, sorted by descending priority fees and tips").
- **P-H2 Validators on `fifo`: send order regardless of fee.** The strategy is per validator and
  not published, so the analysis classifies each validator separately; a mix of both is expected.
- **P-H3 The validator's own client (Agave vs Firedancer) does not change the order** while the
  builder is connected (builder order is kept; `block_stage.rs` L451-500).

### Rakurai

- **P-R1 Ladder: priority order** (reward relative to estimated cost, per its documentation). No
  source is public; any departure is reported as measured.

### Format (all families)

- **P-X1 No systematic order difference between v1 and legacy at equal priority** for Agave and
  Firedancer (both read v1 `priorityFee` from the header: `runtime-transaction/src/transaction_meta.rs`
  L121-176, `fd_pack_cost.h` L88-93). For BAM and Harmonic, whose code is closed, this is an open
  question rather than a prediction.

## What counts as "no rule"

A validator (or family) has *no detectable rule* when the best rule model does not beat random
ordering at α = 0.05 in a permutation test; the report then states the smallest effect the data
could have detected (`analysis/prec_analysis/power.py`). A rule is claimed only with its strictness
(Mallows dispersion and the share of concordant pairs, with bootstrap intervals over batches).
