#!/bin/sh
# Positive control: probes against a local Agave validator, entry boundaries
# from its ledger, then the same analysis as for mainnet.
set -eu
LEDGER=/tmp/ledger
OUT=${LOCALNET_OUT:-/work/localnet}
mkdir -p "$OUT/data" "$OUT/results"
solana-test-validator --version
solana-test-validator --ledger "$LEDGER" --reset --quiet --rpc-port 8899 --bind-address 127.0.0.1 > /tmp/validator.log 2>&1 &
PID=$!
i=0
until curl -s http://127.0.0.1:8899 -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"getHealth"}' | grep -q '"ok"'; do
  i=$((i + 1)); [ "$i" -gt 180 ] && { tail -50 /tmp/validator.log; exit 1; }; sleep 1
done
precedence selftest --rpc http://127.0.0.1:8899 --data "$OUT/data" --reps "${LOCALNET_REPS:-8}" --seed "${LOCALNET_SEED:-1}"
RUN=$(ls -td "$OUT"/data/runs/* | head -1)
kill "$PID"; wait "$PID" || true
precedence localnet-entries --ledger "$LEDGER" --run "$RUN"
# Restart on the same ledger so the stored evidence can be re-checked over RPC,
# including the entry hash chain recomputed from signatures.
solana-test-validator --ledger "$LEDGER" --quiet --rpc-port 8899 --bind-address 127.0.0.1 > /tmp/validator2.log 2>&1 &
PID=$!
i=0
until curl -s http://127.0.0.1:8899 -H 'content-type: application/json'       -d '{"jsonrpc":"2.0","id":1,"method":"getHealth"}' | grep -q '"ok"'; do
  i=$((i + 1)); [ "$i" -gt 180 ] && { tail -50 /tmp/validator2.log; exit 1; }; sleep 1
done
precedence verify --rpc http://127.0.0.1:8899 --data "$OUT/data" --out "$OUT/results/verify.json" || echo "verify reported mismatches"
kill "$PID"; wait "$PID" || true
python -m prec_analysis analyze --data "$OUT/data" --out "$OUT/results"
echo "positive control written to $OUT"
