#!/usr/bin/env bash
# Linux equivalent of run_pilot_windows.cmd -SkipInstall -Profile final/capacity.
# Intended to be started by start_ubuntu_v13.sh in a detached tmux session.
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT" || exit 2
umask 077
PROFILE=${1:-final}
if [[ "$PROFILE" != final && "$PROFILE" != capacity ]]; then
  echo 'Usage: run_ubuntu_v13.sh final|capacity' >&2; exit 2
fi
mkdir -p logs campaigns_v13
# Single campaign at a time from this project; ensures the shared Compose project
# and DB state cannot be interleaved by two local launchers.
exec 9>"$ROOT/.ubuntu_campaign.lock"
if ! flock -n 9; then
  echo 'ERROR: Another Ubuntu Phase 13 campaign is already running.' >&2; exit 5
fi
export PYTHONUNBUFFERED=1 PYTHONUTF8=1
export HDT_COMPOSE_PROJECT=${HDT_COMPOSE_PROJECT:-hdt_iotj_fase13_ubuntu}
export HDT_API_PORT=${HDT_API_PORT:-18020}
export HDT_API_BASE=${HDT_API_BASE:-http://127.0.0.1:${HDT_API_PORT}}
export HDT_PG_PORT=${HDT_PG_PORT:-55441}
export HDT_MQTT_PORT=${HDT_MQTT_PORT:-18892}
if [[ ! "$HDT_API_BASE" == "http://127.0.0.1:${HDT_API_PORT}" ]]; then
  echo "ERROR: HDT_API_BASE must match the loopback API port." >&2; exit 5
fi
STAMP=$(date -u '+%Y%m%d_%H%M%S')
ZIP="$ROOT/IoTJ_Fase13_Ubuntu_${PROFILE}_Resultados_${STAMP}.zip"
START_MARKER="$ROOT/logs/.start_${STAMP}.$$"
touch "$START_MARKER"
LATEST=''
RC=0
finalize() {
  local status=$?
  trap - EXIT
  if [[ $RC -ne 0 ]]; then status=$RC; fi
  # Only package a campaign started in THIS launch, never an older one.
  LATEST=$(find "$ROOT/campaigns_v13" -mindepth 1 -maxdepth 1 -type d -newer "$START_MARKER" -name 'iotj13*' -print 2>/dev/null | sort | tail -1)
  if [[ -n "$LATEST" && -f "$LATEST/plan.json" ]]; then
    echo '== Reproducible Phase 13 report =='
    if [[ -f logs/host_info.txt ]]; then cp logs/host_info.txt "$LATEST/ubuntu_host_info.txt"; fi
    if [[ -f logs/last_ubuntu_log ]]; then
      current_log=$(cat logs/last_ubuntu_log)
      if [[ -f "$current_log" ]]; then cp "$current_log" "$LATEST/ubuntu_launcher.log"; fi
    fi
    .venv/bin/python research/final_report_v13.py "$LATEST" --out "$LATEST/auditoria_fase13" || { [[ $status -ne 0 ]] || status=6; }
    echo '== Packaging entire campaign =='
    .venv/bin/python research/package_campaign_v4.py "$LATEST" --output "$ZIP" || { [[ $status -ne 0 ]] || status=7; }
    printf '%s\n' "$LATEST" > logs/last_campaign_path
  else
    echo 'No new campaign folder found; packaging Ubuntu diagnostics only.'
    FALLBACK_PY=python3
    if [[ -x .venv/bin/python ]]; then FALLBACK_PY=.venv/bin/python; fi
    "$FALLBACK_PY" - "$ZIP" "$ROOT" <<'PY' 
import pathlib,sys,zipfile
out=pathlib.Path(sys.argv[1]); root=pathlib.Path(sys.argv[2])
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
  for f in [root/'logs'/'last_ubuntu_log',root/'logs'/'host_info.txt']:
    if f.is_file(): z.write(f,arcname='diagnostics/'+f.name)
  pointer=root/'logs'/'last_ubuntu_log'
  if pointer.is_file():
    latest=pathlib.Path(pointer.read_text(encoding='utf-8').strip())
    if latest.is_file() and latest.is_relative_to(root/'logs'):
      z.write(latest,arcname='diagnostics/'+latest.name)
PY
    [[ $status -ne 0 ]] || status=8
  fi
  rm -f "$START_MARKER"
  printf '%s\n' "$ZIP" > logs/last_zip_path
  printf '%s\n' "$status" > logs/last_exit_code
  echo "RESULT_ZIP: $ZIP"
  echo "FINAL_STATUS: $status (0=campaign complete; nonzero=failed/partial, logs retained)"
  exit "$status"
}
trap finalize EXIT
trap 'echo "Interrupted"; RC=130; exit 130' INT TERM
printf '%s\n' "$PROFILE" > logs/last_profile
rm -f logs/last_exit_code logs/last_zip_path logs/last_campaign_path
printf 'Starting IoTJ Phase 13 on Ubuntu, profile=%s at %s\n' "$PROFILE" "$(date -u -Is)"
echo "Project: $ROOT"
echo "Compose project: $HDT_COMPOSE_PROJECT"
echo "Host API: $HDT_API_BASE"
{
  uname -a
  . /etc/os-release; echo "$PRETTY_NAME"
  echo '== CPU/memory/disk =='
  nproc
  free -h
  df -h .
  echo '== Python =='
  .venv/bin/python --version
  echo '== Docker =='
  docker version --format '{{.Client.Version}} / {{.Server.Version}}'
  docker compose version
  docker info --format 'CPUs={{.NCPU}} MemoryBytes={{.MemTotal}}'
} > logs/host_info.txt 2>&1
cat logs/host_info.txt
if [[ ! -x .venv/bin/python ]]; then
  echo 'Missing .venv/bin/python. Run ./prepare_ubuntu_v13.sh first.'; RC=2;exit 2
fi
.venv/bin/python research/check_dependencies_v5.py || { RC=2; exit 2; }
echo '== Preflight Docker/Compose/ports =='
.venv/bin/python research/preflight_v4.py --docker || { RC=3; exit 3; }
echo '== Unit tests =='
.venv/bin/python -m unittest discover -s research/tests -v || { RC=4; exit 4; }
echo '== Fixed protocol plan (dry-run) =='
.venv/bin/python research/final_campaign_v13.py || { RC=5; exit 5; }
echo '== Docker campaign =='
if [[ "$PROFILE" == capacity ]]; then
  .venv/bin/python research/final_campaign_v13.py --execute --capacity-only
  RC=$?
else
  .venv/bin/python research/final_campaign_v13.py --execute
  RC=$?
fi
exit "$RC"
