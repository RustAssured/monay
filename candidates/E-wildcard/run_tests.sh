#!/usr/bin/env bash
# Single command: ./run_tests.sh   (needs numpy + scipy: pip install -r requirements.txt)
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -t . -v
