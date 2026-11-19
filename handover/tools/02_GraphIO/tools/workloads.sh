#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$root"
export PYTHONDONTWRITEBYTECODE=1
exec "${PYTHON_BIN:-python3}" -m idfs_workload "$@"
