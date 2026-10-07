#!/usr/bin/env bash
# Single command: offline unit/integration tests (+ live registry test if DEPSCORE_LIVE=1)
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pytest -q tests "$@"
