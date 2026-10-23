#!/usr/bin/env bash
# Run in the candidate repository. Any non-success retrieval is a veto, even
# when old hash-pinned data is internally consistent. set -e propagates vetoes.
set -euo pipefail
sources="${1:?sources required}"
outcome="${2:?retrieval outcome required}"
flags=()
if [[ "$outcome" != "success" ]]; then
  flags+=(--upstream-failed)
fi
python -m src.integrity_audit --root . --triage --sources "$sources" "${flags[@]}"
