#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
export PYTHONPATH="$root/tools${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m idfs_workload.fast_container render "$@"
