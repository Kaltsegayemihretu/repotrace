#!/bin/bash
# Double-click this file in Finder to start RepoTrace.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. Install it from python.org, then double-click this file again."
  read -r -p "Press Return to close."; exit 1
fi
python3 -B -m repotrace "$@"
