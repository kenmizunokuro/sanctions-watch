#!/usr/bin/env bash
# Observe from an independent network using an isolated worktree of origin/main.
set -euo pipefail

mode=${1:---check}
if [[ $# -gt 1 || "$mode" != --check && "$mode" != --publish ]]; then
  echo "Usage: $0 [--check|--publish]" >&2
  exit 2
fi

repo=$(cd "$(dirname "$0")/.." && pwd -P)
python_bin=${UN_WATCH_PYTHON:-"$repo/.venv/bin/python"}
if [[ "$python_bin" != /* || ! -x "$python_bin" ]]; then
  echo "Set UN_WATCH_PYTHON to the absolute path to a Python interpreter with the repository dependencies." >&2
  exit 2
fi
export GIT_TERMINAL_PROMPT=0
mkdir -p "$repo/.runtime"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$repo/.runtime/watch-un-external.lock"
  if ! flock -n 9; then
    echo "Another watch-un-external run is already in progress." >&2
    exit 0
  fi
fi
scratch=$(mktemp -d)
worktree="$scratch/checkout"
cleanup() {
  if [[ -d "$worktree" ]]; then
    git -C "$repo" worktree remove --force "$worktree" 2>/dev/null || true
  fi
  rm -rf "$scratch"
}
trap cleanup EXIT

for attempt in 1 2; do
  git -C "$repo" fetch --no-tags origin +refs/heads/main:refs/remotes/origin/main
  base=$(git -C "$repo" rev-parse refs/remotes/origin/main)
  git -C "$repo" worktree add --detach "$worktree" "$base"
  (
    cd "$worktree"
    "$python_bin" -m src.un_external_poll collect --root .
  )
  if [[ "$mode" == --check ]]; then
    echo "External observation verified. No commit or push performed."
    exit 0
  fi

  git -C "$worktree" add -- data/un/ data/raw/un/ data/source_audit/ data/heartbeat/ \
    data/dashboard/status.csv data/dashboard/un_reviews.csv
  if [[ -f "$worktree/data/dashboard/status_detail.csv" ]]; then
    git -C "$worktree" add -- data/dashboard/status_detail.csv
  fi
  git -C "$worktree" diff --cached --check
  if git -C "$worktree" diff --cached --quiet; then
    exit 0
  fi
  git -C "$worktree" -c user.name='sanctions-watch[bot]' \
    -c user.email='sanctions-watch@users.noreply.github.com' commit \
    -m 'chore(un): 国連の別環境観測を記録'
  if git -C "$worktree" push origin HEAD:refs/heads/main; then
    exit 0
  fi

  remote=$(git -C "$worktree" ls-remote --exit-code origin refs/heads/main)
  remote=${remote%%[[:space:]]*}
  if [[ "$remote" == "$base" || "$attempt" == 2 ]]; then
    exit 1
  fi
  echo "main advanced during observation. Re-fetching and collecting again." >&2
  git -C "$repo" worktree remove --force "$worktree"
done
