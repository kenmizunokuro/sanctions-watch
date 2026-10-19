#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git init --bare -q "$tmp/origin.git"
git init -q -b main "$tmp/work"
cd "$tmp/work"
git config user.name "Crypto Test"
git config user.email "test@example.invalid"
git remote add origin "$tmp/origin.git"
mkdir -p data/crypto
printf '{"run":1}\n' > data/crypto/dashboard.json
printf '{"run":1}\n' > data/crypto/chain_observations.json
printf '{"run":1}\n' > data/crypto/token_history.json
printf '{"run":1}\n' > data/crypto/address_master.json
printf '{"schema_version":1,"commit_sha":"%040d"}\n' 0 > data/crypto/published_commit.json
git add data/crypto/
git commit -qm init
git push -q -u origin main

bash "$repo_root/tools/publish_crypto_dashboard.sh" >/dev/null
python3 - <<'PY'
import json
import subprocess
from pathlib import Path
d = json.loads(Path('data/crypto/published_commit.json').read_text())
assert d['schema_version'] == 1
assert len(d['data_tree_hash']) == 64
expected = subprocess.check_output(['git', 'show', d['commit_sha'] + ':data/crypto/dashboard.json'])
assert expected == Path('data/crypto/dashboard.json').read_bytes()
PY

before="$(git rev-parse HEAD)"
bash "$repo_root/tools/publish_crypto_dashboard.sh" >/dev/null
test "$(git rev-parse HEAD)" = "$before" || { echo "Unexpected no-op commit"; exit 1; }

# Simulate a successful data push followed by a failed pointer push.
printf '{"run":2}\n' > data/crypto/dashboard.json
git add data/crypto/dashboard.json
git commit -qm update-data-only
git push -q origin HEAD:main
bash "$repo_root/tools/publish_crypto_dashboard.sh" >/dev/null
python3 - <<'PY'
import json
import subprocess
from pathlib import Path
d = json.loads(Path('data/crypto/published_commit.json').read_text())
assert d['commit_sha'] == subprocess.check_output(['git','rev-parse','HEAD^'],text=True).strip()
assert subprocess.check_output(['git','show',d['commit_sha'] + ':data/crypto/dashboard.json']) == Path('data/crypto/dashboard.json').read_bytes()
assert json.loads(Path('data/crypto/dashboard.json').read_text())['run'] == 2
PY

# Incomplete publication must stop instead of silently reusing old contents.
rm data/crypto/chain_observations.json
if bash "$repo_root/tools/publish_crypto_dashboard.sh" >"$tmp/failure.log" 2>&1; then
  echo 'Expected incomplete-source rejection' >&2
  exit 1
fi
grep -q 'Missing or empty dashboard artifact' "$tmp/failure.log"
echo 'PASS crypto dashboard publication: first publish, no-op, recovery, missing artifact'
