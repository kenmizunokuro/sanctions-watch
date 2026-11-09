#!/usr/bin/env bash
set -euo pipefail

if [[ "${GITHUB_REF:-}" != refs/heads/main ]]; then
  echo 'Approved vessel history application runs only on main'
  exit 0
fi

if [[ -f data/monitoring/approved_vessel_history_20261109_applied.json ]]; then
  # This is a durable one-time journal, not a requirement to keep the three rows
  # frozen forever. Later legitimate source updates use the normal monitor.
  python - <<'PY'
import json
import subprocess
from pathlib import Path
from src.approved_vessel_history import RECEIPT
from src.vessel_history import APPROVAL_SHA256
body = Path(RECEIPT).read_bytes()
receipt = json.loads(body)
if (not isinstance(receipt, dict) or receipt.get('status') != 'APPLIED'
        or receipt.get('count') != 3 or receipt.get('approval_sha256') != APPROVAL_SHA256):
    raise ValueError('Approved vessel history application journal is invalid')
committed = subprocess.check_output(['git', 'show', 'HEAD:' + RECEIPT.as_posix()])
if committed != body:
    raise ValueError('Application journal has unpublished working-tree changes')
subprocess.run(['git', 'fetch', 'origin', '+refs/heads/main:refs/remotes/origin/main'],
               check=True, capture_output=True)
published = subprocess.check_output(['git', 'show', 'origin/main:' + RECEIPT.as_posix()])
if published != body:
    raise ValueError('Application journal is not published on remote main')
print('Approved three-vessel history was already published')
PY
  exit 0
fi

# Never include pre-existing candidate/staged changes in this separate commit.
git diff --quiet
git diff --cached --quiet
python -m src.approved_vessel_history --apply --root .
git add -- \
  data/master/master.csv \
  data/review/integrity_retention_decisions.json \
  data/dashboard/list.csv \
  data/dashboard/screening.csv.gz \
  data/dashboard/changes.csv \
  data/diff/latest.csv \
  data/evidence/ofac_vessel_name_history.csv \
  data/monitoring/approved_vessel_history_20261109_applied.json
if ! git diff --cached --quiet; then
  git -c user.name='sanctions-watch[bot]' \
      -c user.email='sanctions-watch@users.noreply.github.com' \
      commit -m 'fix(master): retain three approved historical vessel names'
  # A non-fast-forward push is a failed publication, never an automatic rebase.
  git push origin HEAD:refs/heads/main
fi
