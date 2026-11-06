#!/usr/bin/env bash
# Starts the 63-trial plan in a detached TMUX session, independent of SSH.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT"
PROFILE=${1:-final}
if [[ "$PROFILE" != final && "$PROFILE" != capacity ]]; then
  echo 'Usage: ./start_ubuntu_v13.sh [final|capacity]' >&2; exit 2
fi
command -v tmux >/dev/null || { echo 'tmux missing: sudo apt install tmux'; exit 2; }
[[ -x .venv/bin/python ]] || { echo 'First run: bash prepare_ubuntu_v13.sh'; exit 3; }
SESSION=iotj13_campaign
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Campaign session already active: $SESSION. Do not start a duplicate."
  exit 4
fi
mkdir -p logs
LOG="$ROOT/logs/ubuntu_${PROFILE}_$(date -u +%Y%m%d_%H%M%S).log"
printf '%s\n' "$LOG" > "$ROOT/logs/last_ubuntu_log"
# Bash-escaped absolute args make paths containing spaces safe.
printf -v RUN_COMMAND '%q ' bash "$ROOT/run_ubuntu_v13.sh" "$PROFILE"
printf -v QLOG '%q' "$LOG"
tmux new-session -d -s "$SESSION" -c "$ROOT" "$RUN_COMMAND >> $QLOG 2>&1"
echo "Detached tmux session: $SESSION"
echo "Log: $LOG"
echo 'You may close SSH. The campaign will keep running unless the server or tmux is stopped.'
echo 'Progress: ./status_ubuntu_v13.sh'
echo "Attach: tmux attach -t $SESSION (detach with Ctrl+B, then D)"
