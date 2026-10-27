#!/usr/bin/env bash
# Run in the candidate repository. Any non-success retrieval is a veto, even
# when old hash-pinned data is internally consistent. set -e propagates vetoes.
set -euo pipefail
sources="${1:?sources required}"
outcome="${2:?retrieval outcome required}"
case "$sources" in
  mof|ofac|all) ;;
  *) echo "unknown retrieval scope: $sources" >&2; exit 2 ;;
esac
flags=()
if [[ "$outcome" != "success" ]]; then
  flags+=(--upstream-failed)
fi
# This scope describes retrieval, while every job publishes the same combined
# master. Always verify both mandatory official sources before publication.
# Source-specific CLI audits remain available for diagnostics only.
python -m src.integrity_audit --root . --triage --sources all "${flags[@]}"
