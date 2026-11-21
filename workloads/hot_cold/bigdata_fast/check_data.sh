#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
for arg in "$@"; do
  case "$arg" in
    --bundle|--bundle=*)
      export PYTHONPATH="$root/tools${PYTHONPATH:+:$PYTHONPATH}"
      export PYTHONDONTWRITEBYTECODE=1
      exec "${PYTHON_BIN:-python3}" -m idfs_workload.fast_container check "$@"
      ;;
  esac
done
exec "$root/tools/workloads.sh" verify --case fast "$@"
