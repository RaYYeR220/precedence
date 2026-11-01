#!/bin/sh
# Positive control: probes against a local Agave validator, entry boundaries
# from its ledger, then the same analysis as for mainnet.
set -eu
LEDGER=/tmp/ledger
OUT=${LOCALNET_OUT:-/work/localnet}
mkdir -p "$OUT/data" "$OUT/results"
agave-validator --version || true
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
python -m prec_analysis analyze --data "$OUT/data" --out "$OUT/results"
echo "positive control written to $OUT"
