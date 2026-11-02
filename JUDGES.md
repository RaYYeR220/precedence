# Reviewing this in five minutes

1. **The answer.** [README → Short answer](README.md#short-answer): one row per client family with
   the rule, how strictly it held, and which component applied it. The same per validator:
   [REPORT §2](REPORT.md#2-results-by-client-family).

2. **See one block with your own eyes.** Open the [explorer](https://rayyer220.github.io/precedence/)
   (or `site/dist/index.html` locally), click a family, pick any batch. Each block is drawn as a strip
   of all its transactions; ours are the highlighted cells (top number = fee rank, bottom = send
   order). Every strip links to Solscan and has a copyable `getBlock` command.

3. **Check a claim against the chain yourself.** Any probe in `data/runs/*/probes.jsonl` has its
   signature; `data/runs/*/statuses.json` its slot. Then:

   ```
   curl -s https://api.mainnet-beta.solana.com -H 'content-type: application/json' -d \
   '{"jsonrpc":"2.0","id":1,"method":"getBlock","params":[SLOT,{"encoding":"json","transactionDetails":"signatures","rewards":true,"maxSupportedTransactionVersion":1}]}'
   ```

   The signature's index in `result.signatures` is the position we used; the `Fee` reward's pubkey
   is the leader that produced the block.

4. **Check all of them at once.** `docker compose up` (no keys, nothing spent) rebuilds every
   number from the raw data, byte for byte, then re-checks every probe position, every block's
   producer and full transaction order, and every recorded entry hash chain against the public
   RPC. The entry check matters because `getBlock` does not expose entries: we recorded them from a
   gRPC stream, and the verifier recomputes each block's entry hashes from its transaction
   signatures and requires the chain to end exactly on the block's blockhash.

5. **Check that we did not fit the story to the data.** [docs/PREDICTIONS.md](docs/PREDICTIONS.md)
   was committed before the first transaction (compare its commit time with the run start times in
   [REPORT §1](REPORT.md#1-when-and-where-every-measurement-was-taken)). [REPORT §3](REPORT.md#3-the-predictions-checked)
   marks each prediction confirmed, refuted or inconclusive; several are refuted, and say so.

6. **Check that the pipeline can find a rule when there is one, and only then.**
   - [localnet/CONTROL.md](localnet/CONTROL.md): a local Agave 4.3.2 validator, whose scheduler is
     open, run through the same pipeline; it recovers the rule the code describes.
   - [REPORT §10](REPORT.md#10-noise-controls-and-power): shuffled positions yield no rule; an inert
     variable predicts nothing; every "no rule" comes with the smallest effect it could have seen.
   - `python -m prec_analysis validate`: simulated validators with known rules are recovered.

7. **Engineering.** Dry run by default; live needs a mounted key, an environment acknowledgement
   and a flag ([README → Live runs](README.md#live-runs-spend-cap-and-keys)). Every lamport is in
   [`data/ledger/spend.jsonl`](data/ledger/spend.jsonl), reserved at worst case before sending and
   settled after; the total matches the wallets' on-chain balance change. Structured logs per run
   in `data/runs/*/log.jsonl`. Tests: `docker compose --profile test run --rm test`.

What we could not separate, and what went wrong, is stated plainly in
[README → What this design does not separate](README.md#what-this-design-does-not-separate) and
[REPORT §13](REPORT.md#13-what-went-wrong).
