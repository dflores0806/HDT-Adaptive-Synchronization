#!/usr/bin/env bash
# Once-only setup with a Python environment local to this Ubuntu project.
# No Docker privileges escalated and no experiment runs.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT"
if ! command -v python3 >/dev/null; then
  echo 'Python 3 missing; install python3.12, python3.12-venv, python3-pip via apt.' >&2; exit 2
fi
PYBIN=${HDT_PYTHON:-python3}
"$PYBIN" - <<'PY'
import sys
assert sys.version_info[:2] in ((3,11),(3,12)), f'Expected Python 3.11/3.12; found {sys.version}'
print('Python compatible:',sys.version.split()[0])
PY
if [[ ! -x .venv/bin/python ]]; then
  "$PYBIN" -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python research/check_dependencies_v5.py
if ! command -v docker >/dev/null; then
  echo 'Docker missing. Run: bash install_docker_ubuntu.sh'; exit 3
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Docker Engine unavailable for this SSH user. Check service, socket permissions and group membership.' >&2
  echo 'If Docker group was just granted, fully log out and reconnect over SSH.' >&2
  exit 4
fi
if ! docker compose version; then
  echo 'Docker Compose v2 plugin required.' >&2;exit 5
fi
echo 'Ready. Start using: ./start_ubuntu_v13.sh final'
