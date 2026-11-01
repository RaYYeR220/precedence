# Precedence

**Who decides the order inside a Solana block, and by what rule.** We send our own transactions
into the leader windows of chosen mainnet validators, read the blocks back, and for each
validator name the rule its block followed, how strictly it held, and which component applied
it: the validator's scheduler, its parallel execution threads, or an external sequencer or
builder. All measurements are post-Alpenglow (slots ≥ 459,653,000, Votor live, fast leader
handover not active).

<!-- results:headline:start -->
- **0** of 0 probes landed (–), in 0 leader windows over 0 runs and 0 blocks.
- Pre-registered predictions: **0 confirmed**, 0 refuted, 20 inconclusive of 20.
<!-- results:headline:end -->

Most stake no longer orders its own blocks. Validators running BAM (`AgaveBam`, `FireBAM`)
execute a sequence produced by a BAM node, and Harmonic validators (`HarmonicAgave`,
`HarmonicFiredancer`) execute a block from an external builder; their gossip TPU address belongs
to that external party. So the bounty's question, *by what rule does this validator order
transactions*, splits into two that this study answers separately: **which component decided
the order** and **what rule that component applied**.

## Short answer

<!-- results:verdicts:start -->
_No results yet: run `python -m prec_analysis analyze` first._
<!-- results:verdicts:end -->

- Dated measurements, per-validator results and what went wrong: [REPORT.md](REPORT.md)
- Every probe block drawn as a strip, with links to the chain: [explorer](https://rayyer220.github.io/precedence/) (also `site/dist/index.html`)
- Predictions from the clients' source code, committed before the first transaction: [docs/PREDICTIONS.md](docs/PREDICTIONS.md)
- Positive control on a local Agave validator: [localnet/CONTROL.md](localnet/CONTROL.md)
- Reviewing in five minutes: [JUDGES.md](JUDGES.md) · every claim and its evidence: [CLAIMS.md](CLAIMS.md)

## Run it

```
docker compose up
```

One command, no keys, nothing spent. It:

1. rebuilds every number in `results/`, this README, `REPORT.md`, the explorer and the localnet
   control from the raw data in `data/` and `localnet/data/`, and fails if a single byte differs;
2. re-fetches every block that holds one of our transactions from the public mainnet RPC and
   checks each transaction's position, the block's producer, the block's whole transaction order
   and, where entries were recorded, the entry hash chain against the blockhash;
3. runs a short mainnet dry run: cluster snapshot, target selection, batches planned and signed
   for the targets' next leader windows, QUIC connections opened to their TPUs, nothing sent.

| Command | What it does |
|---|---|
| `docker compose --profile test run --rm test` | Rust and Python test suites |
| `docker compose --profile localnet run --rm localnet` | the positive control on a local Agave 4.3.2 validator |
| `docker compose --profile tools run --rm tools precedence status` | balances and the spend ledger |
| `docker compose --profile live run --rm live` | a real measurement run (see [Live runs](#live-runs-spend-cap-and-keys)) |

## Why this design

A block's transaction order is the product of several components, and a probe that only reads
positions mixes them all. The design gives each component its own instrument.

**Four probe kinds, each a test of one question** (all from `crates/precedence/src/design.rs`;
fees and send order are drawn from a seeded RNG and written to `design.json`, with its hash
logged, before the first transaction leaves the machine):

| Probe | What varies | Question it answers |
|---|---|---|
| Ladder | 8 probes, fees 0–10,000 lamports, send order an independent permutation, sent together | among transactions queued at the same time, does fee decide? |
| Tie | 8 probes, equal fee, sent 0.5, 2 or 5 ms apart | with priority tied, oldest first, newest first, or neither? |
| Sweep | 12 probes 10 or 20 ms apart, fee **rising** with send time | over tens of ms, does arrival win, does fee win, or is time cut into batches of length w? |
| Format | 4 v1 + 4 legacy transactions at equal priority | does the wire format matter? |

**Two arms.** In `distinct` batches every probe has its own fee payer and writes nothing else,
so a leader may run them in parallel. In `shared` batches every probe also write-locks one
shared account, so the leader must run them one after another. The difference between the arms
isolates what parallel execution does to the order.

**Entry boundaries.** A block is a sequence of entries, each a batch the leader recorded at
once. `getBlock` does not return them; a Yellowstone gRPC stream does, and we store them. Two of
our probes in the same entry were handed to one execution thread together, so their order is the
scheduler's decision; two probes in different entries are ordered by when the threads finished.
Entries are not taken on trust: `verify` recomputes each block's entry hash chain from its
transaction signatures and requires it to end on the block's blockhash.

**Pre-registered predictions.** Before sending anything we read each client's scheduler and wrote
down what each probe should show for it ([docs/PREDICTIONS.md](docs/PREDICTIONS.md), with file and
line references). The report marks each one confirmed, refuted or inconclusive.

**A rule is a model that beats random.** For each validator and each question, candidate rules
(priority order, send order, newest-first ties, batches of w ms, random) are scored with a Mallows
model on the observed orders of our probes; a rule is claimed only when it beats random ordering
in a permutation test, and its strictness is reported as the share of concordant pairs with a
bootstrap interval over batches. When nothing beats random, the report says "no rule detectable"
and gives the smallest effect the data could have detected. The same pipeline was run on
simulated data with known rules (`analysis/prec_analysis/validate.py`) and on a real local Agave
validator (below) before it was pointed at mainnet.

## What we had to account for

### Leader schedule and leader types

`getLeaderSchedule` gives each validator's slots; post-Alpenglow a leader still owns 4
consecutive slots aligned to `slot / 4 · 4`, and we only target windows a validator owns
entirely. The client of each validator is its gossip `clientId` and version (`getClusterNodes`),
snapshotted at the start of every run and every 10 minutes during it, because the id flips
between `JitoLabs` and `AgaveBam` as a validator's BAM connection comes and goes. Evidence is
always attributed to the validator that actually produced the block (the `Fee` reward of the
block), never to the one we aimed at.

<!-- results:leader_types:start -->
_No run in the data directory._
<!-- results:leader_types:end -->

Timing: after Alpenglow a window is not four equal slots. Measured from the first-shred
timestamps of the RPC node (`slotsUpdates`), slots start at fixed offsets inside a window, and the
boundary between windows waits for the previous window's certificate, so window starts drift like
a random walk. The harness predicts window starts from the latest observed window plus the median
recent period, and every analysis uses the *measured* send time relative to the window's *actual*
start, not the intended one.

<!-- results:clock:start -->
_No run in the data directory._
<!-- results:clock:end -->

### The path from our backend to the executing node, and everything in between

Primary path: direct QUIC from our host to the target's advertised `tpuQuic`, one
unidirectional stream per transaction, from an unstaked client (a throwaway self-signed
certificate), with the connection opened before the window so the handshake is not part of what
we measure. Each stream is timestamped in microseconds when it opens. Control path: an RPC node's
`sendTransaction` with `skipPreflight` and `maxRetries: 0`, which adds an HTTP hop and the node's
forwarding.

The address is part of the answer. For BAM and Harmonic validators `tpuQuic` is not the
validator: it is a BAM node or a Harmonic remote TPU (the IP differs from the gossip IP and is
shared by dozens of validators). Our transactions therefore enter the external orderer, which is
exactly the component whose rule we then measure. We do not use services that require tips (they
add a write lock on a shared tip account and change the order we are measuring), and we send no
bundles.

### Interaction of our own transactions with each other

Within a batch every probe has the same cost (same instructions, same compute and loaded-data
limits; verified against `costUnits` in the block), so priority order equals fee order. Every
probe carries a unique 12-byte memo tag and, in `distinct` batches, its own fee payer, so no two
probes conflict or deduplicate. The `shared` arm adds exactly one conflict on purpose. Batches
aimed at different windows are at least two windows apart, so probes of different batches never
compete in one block.

### Competition inside the block

We compare only our own probes with each other. Foreign transactions between them are counted,
and the whole block is stored (position, priority, cost, writable accounts) so the competition
each batch faced is visible. As a supplementary view, the report also correlates priority with
position for *all* transactions of every collected block: that is what an observer without
probes sees, and it is the baseline our probe-based classification is compared against.

### Send order

Send order is randomized independently of fee in every ladder and format batch and recorded per
probe. Ties isolate it (fee held equal), and the sweep sets it against fee on purpose (every later
probe pays more), which is what lets a batch auction be told apart from both plain arrival order
and plain priority order.

### Noise versus rule

The unit of evidence is a pair of our probes in the same block. Pairs within a batch are not
independent, so every interval is a bootstrap over batches and every p-value a permutation of
positions within batches. Negative controls: positions shuffled within each block must yield no
rule, and an inert variable (the parity of the probe index) must not predict position. A "no
rule" verdict comes with its minimum detectable effect.

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

## What this design does not separate

- **Send time versus arrival time at the leader.** We control and record when a probe leaves our
  host, not when it reaches the leader. Ties sent 0.5 ms apart can arrive swapped; the analysis
  works with measured send times and reports results per spacing.
- **What happens inside a closed external orderer.** For BAM and Harmonic we measure the order
  their blocks put our probes in, and which simple rule explains it (for instance batches of w ms
  sorted by priority). We cannot see their code, so "batch auction of 50 ms" is a description of
  behaviour, not a reading of their source.
- **Validator software versus operator configuration.** Two validators on the same client can run
  different flags, worker counts, builder strategies (Harmonic) or relayers. We report per
  validator and per family; a family verdict describes its typical validator.
- **Stake-weighted QoS.** We send as an unstaked client. A staked sender may land differently and
  might see different ordering at the margin.
- **Bundles and tips.** Out of scope by the bounty's rules; we send none. Bundles of others can sit
  between our probes; they never reorder ours relative to each other.
- **Time.** Rules can change without a client upgrade (an external builder can change its
  strategy). Every result is dated, and the measurement was repeated on a second day.

## Positive control and validation of the analysis

On a local Agave 4.3.2 validator, whose scheduler is open source, the same probes, sender,
collector and analysis recover the rule the code describes; see
[localnet/CONTROL.md](localnet/CONTROL.md). On simulated data with known rules, model selection
recovers the generating rule (`python -m prec_analysis validate`). That simulation is also why the
sweep raises the fee with send time: with the fee falling instead, arrival order and priority
order predict the same order and a batch auction cannot be identified.

## Live runs, spend cap and keys

- **Dry run is the default.** A live run needs all three: a keypair mounted read-only at
  `keys/burner.json` → `/keys/burner.json`, `PRECEDENCE_LIVE=i-accept-spending-sol` in `.env`, and
  the `--live` flag (the `live` profile passes it). Missing any one of them gives a dry run, with
  the reason logged.
- **Spend cap.** Every transaction, including funding and sweeping, is charged at its worst-case
  fee in `data/ledger/spend.jsonl` and fsynced *before* it leaves the process; a batch is reserved
  whole or not at all. Default cap 0.05 SOL, hard maximum 0.06 SOL; a later run can lower the cap
  stored in the ledger but never raise it.
- **Keys** never enter the image or the repository (`.gitignore`, `.dockerignore`). Fee payers for
  probes are derived deterministically from the mounted key, so `sweep` can always return their
  balances.

```
cp .env.example .env                                            # set PRECEDENCE_LIVE and, optionally, the gRPC endpoint
docker compose --profile tools run --rm tools precedence fund --live
docker compose --profile live run --rm live
docker compose --profile tools run --rm tools precedence sweep --live
```

## Repository layout

```
crates/precedence/   the harness (Rust): probe transactions, design, planner, slot clock,
                     QUIC/RPC senders, spend ledger, collector, entry recorder, verifier, CLI
analysis/            the analysis (Python): tidy, rule models, rule cards, controls, power,
                     fingerprint, simulation validation, report and explorer rendering
data/                raw evidence: runs/<run>/ (design, windows, probes, statuses, entries,
                     snapshots, logs), blocks/<slot>.json.gz, ledger/spend.jsonl
results/             analysis output (JSON), rebuilt and diffed by `docker compose up`
localnet/            the positive control: script, raw data, results, write-up
site/dist/           the explorer (static, generated)
docs/                DATA.md (data dictionary), PREDICTIONS.md (pre-registered predictions)
```

MIT licensed.
