#!/usr/bin/env bash
# Publish a content-complete, immutable dashboard version. Run in the checked-out main workflow.
set -euo pipefail
for file in data/crypto/dashboard.json data/crypto/chain_observations.json data/crypto/token_history.json; do
  if [ ! -s "$file" ]; then
    echo "Missing or empty dashboard artifact: $file" >&2
    exit 1
  fi
done

git add data/crypto/
if ! git diff --cached --quiet; then
  git commit -m 'chore(crypto): アドレス台帳と監視証跡を更新'
  git push origin HEAD:main
fi

# Only tracked content hashes, not commit ancestry, determine whether the pointer is stale.
# This works with actions/checkout's shallow clone and heals a previous pointer-push failure.
data_hash="$(git ls-files -s -- data/crypto/ | grep -v 'data/crypto/published_commit.json$' | sha256sum | cut -d' ' -f1)"
if [ ! -n "$data_hash" ] || [ "${#data_hash}" -ne 64 ]; then
  echo 'Dashboard data digest is missing' >&2
  exit 1
fi
previous_hash="$(python3 - <<'PY'
import json
from pathlib import Path
try:
    p = json.loads(Path('data/crypto/published_commit.json').read_text())
    print(p.get('data_tree_hash', '') if p.get('schema_version') == 1 else '')
except (OSError, ValueError, TypeError, AttributeError):
    print('')
PY
)"
if [ "$data_hash" = "$previous_hash" ]; then
  echo 'Published dashboard already points to current data'
  exit 0
fi

# Push data first; only after that succeeds publish the corresponding commit SHA.
data_commit="$(git rev-parse HEAD)"
python3 - "$data_commit" "$data_hash" <<'PY'
import json
import sys
from pathlib import Path
sha, digest = sys.argv[1:]
assert len(sha) == 40 and all(c in '0123456789abcdef' for c in sha)
assert len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
Path('data/crypto/published_commit.json').write_text(
    json.dumps({'schema_version': 1, 'commit_sha': sha, 'data_tree_hash': digest},
               ensure_ascii=False, separators=(',', ':')) + '\n')
PY
git add data/crypto/published_commit.json
git commit -m 'chore(crypto): 同一世代の公開版を確定'
git push origin HEAD:main
