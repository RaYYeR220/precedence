# Data dictionary

Everything the analysis uses is a file under `data/`. Nothing is fetched at analysis time.
Times are microseconds since the Unix epoch (`*_unix_us`) taken from the sender host's clock,
which is NTP-synced; durations are microseconds (`*_us`). Slots are mainnet slot numbers.
Every run directory is self-contained.

```
data/
  runs/<run_id>/
    config.json          run parameters
    design.json          every batch's design, written and hashed before the first send
    snapshot/            raw RPC responses (getClusterNodes, getVoteAccounts, getLeaderSchedule, getEpochInfo, features, BAM leader slots)
    windows.jsonl        one line per batch: targeting, timing and path measurements
    probes.jsonl         one line per probe transaction
    statuses.json        signature -> landed slot (getSignatureStatuses)
    slots.jsonl          slot-clock observations
    entries.jsonl        entry boundaries + receive times for the targeted slots (Yellowstone gRPC)
    footers.jsonl        block footer (producer time, user agent) where the stream provides it
    log.jsonl            structured log of the run
  blocks/<slot>.json.gz  trimmed getBlock for every slot that holds a probe or was targeted
  ledger/spend.jsonl     every lamport reserved and settled
```

## `config.json`

| field | type | meaning |
|---|---|---|
| `run_id` | string | `YYYYMMDDTHHMMZ-<6 hex>` |
| `mode` | `"live"` \| `"dry"` | dry runs build and sign but never write to the network |
| `run_seed` | u64 | seed for every batch (`batch_seed(run_seed, batch_id)`) |
| `rpc_url`, `ws_url`, `grpc_url` | string | endpoints used (no secrets) |
| `cap_lamports` | u64 | spend cap in force for this run |
| `cu_limit`, `loaded_data_limit` | u32 | identical for every probe of the run |
| `ladder8`, `ladder12`, `tie_fee`, `format_levels` | u64[] | fee constants |
| `git_commit` | string | commit of the harness that produced the run |
| `started_unix_us`, `ended_unix_us` | u64 | |

## `design.json`

```json
{ "run_id": "...", "run_seed": 1, "sha256": "<hash of the batches array>",
  "batches": [ { "batch_id": 0, "target": "<identity>", "family": "AgaveBam", "version": "4.3.2",
                 "window_first_slot": 463900000, "path": "quic",
                 "design": { "kind": "ladder|tie|sweep|format", "arm": "distinct|shared",
                             "anchor": {"type": "before_window", "slots": 1.0},
                             "spacing_us": 0, "seed": 123,
                             "probes": [ {"idx":0,"priority_fee":150,"format":"v1","send_rank":3,"send_offset_us":0,"payer_slot":7} ] },
                 "design_hash": "<sha256 of design>" } ] }
```

`family` is the gossip `clientId` of the target at planning time. `path` is `quic` (direct QUIC to the
target's advertised `tpuQuic`) or `rpc` (`sendTransaction`, `skipPreflight`, `maxRetries: 0`).

## `windows.jsonl`

| field | type | meaning |
|---|---|---|
| `batch_id` | u32 | |
| `target`, `family`, `version` | string | as planned |
| `window_first_slot` | u64 | first slot of the target's 4-slot window |
| `tpu_quic` | string | address the probes were sent to |
| `tpu_is_proxy` | bool | `tpuQuic` IP differs from the gossip IP (BAM node, Harmonic remote TPU, relayer) |
| `path` | `quic` \| `rpc` | |
| `slot_us` | f64 | slot length from the clock fit at send time |
| `predicted_window_start_unix_us` | u64 | |
| `first_send_unix_us` | u64 \| null | null when skipped |
| `rtt_us`, `handshake_us` | u64 \| null | QUIC path measurements |
| `blockhash`, `blockhash_slot` | string, u64 | lifetime used by every probe of the batch |
| `status` | string | `sent`, `dry`, or `skipped:<reason>` |

## `probes.jsonl`

| field | type | meaning |
|---|---|---|
| `batch_id`, `idx` | u32, u16 | `idx` matches `design.probes[].idx` |
| `signature` | string | base58 |
| `payer` | string | fee payer (distinct per probe inside a batch) |
| `shared_write` | string \| null | account write-locked by every probe of a `shared` batch |
| `priority_fee` | u64 | lamports |
| `format` | `v1` \| `legacy` | |
| `est_cost` | u64 | cost-model units the leader sees before execution |
| `agave_priority` | u64 | `(priority_fee + 2500)·1e6 / (est_cost + 1)` |
| `send_rank` | u16 | position in the send sequence (0 = first) |
| `planned_offset_us` | i64 | planned send time relative to the batch's first send |
| `sent_unix_us` | u64 \| null | when the QUIC stream (or HTTP request) was opened |
| `sent_done_unix_us` | u64 \| null | when it finished |
| `send_err` | string \| null | |

## `statuses.json`

`{ "<signature>": {"slot": 463900002, "err": null} | null }`. `null` = not found after the
collection wait (did not land).

## `slots.jsonl`

`{"slot": 463900001, "recv_unix_us": ..., "source": "ws|grpc", "status": "processed|..."}`.

## `entries.jsonl`

| field | type | meaning |
|---|---|---|
| `slot` | u64 | |
| `index` | u32 | entry index within the slot |
| `starting_transaction_index` | u32 | index in the block of the entry's first transaction |
| `executed_transaction_count` | u32 | transactions in the entry (0 = tick) |
| `num_hashes` | u64 | |
| `hash` | string | entry hash, base58 |
| `created_at_unix_us` | u64 \| null | provider timestamp of the update |
| `recv_unix_us` | u64 | when our recorder received it |

For a block, the entries with `executed_transaction_count > 0` partition `0..n_tx` contiguously.
`verify` checks this against `getBlock`.

## `footers.jsonl`

`{"slot": ..., "producer_time_ns": ..., "user_agent": "agave/4.3.2"}` (self-declared by the producer).

## `blocks/<slot>.json.gz`

```json
{ "slot": 463900002, "blockhash": "...", "previous_blockhash": "...", "parent_slot": 463900001,
  "block_time": 1793490000, "block_height": 441900000, "leader": "<Fee reward pubkey or null>",
  "raw_sha256": "<sha256 of the full getBlock JSON response>",
  "txs": [ { "i": 0, "sig": "...", "version": "legacy|0|1", "fee": 5000, "cost_units": 1481,
             "cu_consumed": 150, "priority_fee": 0, "cu_limit": 200000, "n_sigs": 1,
             "payer": "...", "writable": ["..."], "err": false } ] }
```

`i` is the transaction's index in the block, i.e. its position in ledger order. `leader` is the
pubkey of the block's `Fee` reward.

## `ledger/spend.jsonl`

`{"ts_unix_us": ..., "kind": "reserve|settle|fund|sweep", "run_id": "...", "batch_id": 0,
"signature": "...", "lamports": 9000}`. Reservations are worst case (base fee + full priority fee)
and are written and fsynced before the transaction leaves the process.
