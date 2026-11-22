#!/bin/bash
# Browser check of the SAVED demo (prototype/lab/index.html): same checks as the live run, minus the project picker.
# It is served over a local web server because browsers will not let a page frame another local file.
#   qa/browser/run_lab_saved.sh OUTDIR
cd "$(dirname "$0")/../.." || exit 1
OUT="${1:?output folder}"; PORT=$((8900 + RANDOM % 100)); mkdir -p "$OUT"
python3 -m http.server $PORT --bind 127.0.0.1 > "$OUT/server.log" 2>&1 &
SERVER=$!; sleep 1
SAVED_URL="http://127.0.0.1:$PORT/prototype/lab/index.html" node qa/browser/lab_live.mjs "$OUT" "http://127.0.0.1:$PORT/" StudentManagement step-06 step-07 '{"concerns":1,"tests":12,"mustMention":["fetch_course_by_id","enrollment_service.py"]}'
STATUS=$?; kill $SERVER 2>/dev/null; exit $STATUS
