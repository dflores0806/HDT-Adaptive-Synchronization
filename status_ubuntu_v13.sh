#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT"
SESSION=iotj13_campaign
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo 'STATE: RUNNING (tmux session active)'
else
  echo 'STATE: not running (finished, stopped, or not yet started)'
fi
if [[ -f logs/last_ubuntu_log ]]; then
  LOG=$(cat logs/last_ubuntu_log)
  if [[ -f "$LOG" ]]; then
    echo "== Most recent launcher output: $LOG =="
    tail -n 25 "$LOG"
  fi
fi
if [[ -f logs/last_exit_code ]]; then
  echo "Exit code: $(cat logs/last_exit_code)"
fi
if [[ -f logs/last_zip_path ]]; then
  echo "ZIP: $(cat logs/last_zip_path)"
fi
# Read the *campaign manifest* directly; not a guess from log timestamps.
if [[ -d campaigns_v13 ]]; then
  LATEST=$(find campaigns_v13 -mindepth 1 -maxdepth 1 -type d -name 'iotj13*' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)
  if [[ -n "$LATEST" && -r "$LATEST/plan.json" && -x .venv/bin/python ]]; then
    .venv/bin/python - "$LATEST/plan.json" <<'PY'
import json,sys
p=sys.argv[1]
try:
 d=json.load(open(p,encoding='utf8'))
 from collections import Counter
 states=Counter(x.get('status') for x in d['tasks'])
 print('Campaign:',d.get('campaign_id'))
 print('Progress:',dict(states),'planned=',len(d['tasks']))
 print('Gate:',d.get('gate',{}).get('status'),d.get('gate',{}).get('reason',''))
except Exception as e: print('Could not read manifest:',repr(e))
PY
  fi
fi
