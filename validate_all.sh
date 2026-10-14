#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$root"
export PYTHONDONTWRITEBYTECODE=1
./workloads.sh validate
python3 -m unittest discover -s tests -v
while IFS= read -r -d '' script; do bash -n "$script"; done < <(find . -name '*.sh' -print0)
