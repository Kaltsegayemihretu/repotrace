#!/bin/bash
# Browser check of the LIVE guided screens on one or more projects (needs Chrome + Node 22+ and the projects on disk).
#   qa/browser/run_lab.sh OUTDIR PROJECT BASE HEAD ['{"concerns":1,"tests":12,"mustMention":["fetch_course_by_id"]}']
# PROJECT is a Git folder; BASE and HEAD are text that appears in the version list (a tag name's commit message, or a short hash).
cd "$(dirname "$0")/../.." || exit 1
OUT="${1:?output folder}"; PROJECT="${2:?project folder}"; BASE="${3:?base text}"; HEAD="${4:?head text}"; EXPECT="${5:-}"
PORT=$((8800 + RANDOM % 100)); mkdir -p "$OUT"
REPOTRACE_RECENT="$OUT/recent.json" python3 -B -m repotrace app --no-browser --port $PORT > "$OUT/server.log" 2>&1 &
SERVER=$!; sleep 1.5
if [ -n "$EXPECT" ]; then node qa/browser/lab_live.mjs "$OUT" "http://127.0.0.1:$PORT/" "$PROJECT" "$BASE" "$HEAD" "$EXPECT"; else node qa/browser/lab_live.mjs "$OUT" "http://127.0.0.1:$PORT/" "$PROJECT" "$BASE" "$HEAD"; fi
STATUS=$?; kill $SERVER 2>/dev/null; exit $STATUS
