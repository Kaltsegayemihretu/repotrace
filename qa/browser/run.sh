#!/bin/bash
# Browser check of the real app (needs Google Chrome and Node 22+). Usage: qa/browser/run.sh [light|dark] [outdir]
# Writes screenshots and the exported page to outdir; exits non-zero on any failed step or page error.
cd "$(dirname "$0")/../.." || exit 1
MODE="${1:-light}"; OUT="${2:-$(mktemp -d)}"; PORT=8765; mkdir -p "$OUT"
REPOTRACE_RECENT="$OUT/recent.json" python3 -B -m repotrace app --no-browser --port $PORT > "$OUT/server.log" 2>&1 &
SERVER=$!; sleep 1.5
node qa/browser/drive_app.mjs "$OUT" "http://127.0.0.1:$PORT/" "$MODE"; STATUS=$?
kill $SERVER 2>/dev/null
echo "screenshots: $OUT"; exit $STATUS
