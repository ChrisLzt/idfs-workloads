#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$root"
export PYTHONDONTWRITEBYTECODE=1
./workloads.sh validate
./workloads.sh validate --case baleen
python3 -m unittest discover -s tests -v
while IFS= read -r -d '' script; do bash -n "$script"; done < <(find "$root/.." -name '*.sh' -print0)
