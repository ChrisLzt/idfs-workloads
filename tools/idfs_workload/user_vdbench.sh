#!/usr/bin/env bash
set -euo pipefail
runtime_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
configured_home='@STOCK_HOME@'
if [[ "$runtime_dir" != '@RUNTIME_DIR@' || ${VDBENCH_HOME:-$configured_home} != "$configured_home" ]]; then
  printf '%s\n' 'Render again for this bundle path and Vdbench installation.' >&2
  exit 2
fi
require_sha() {
  local observed
  observed=$(sha256sum -- "$1")
  if [[ ${observed%% *} != "$2" ]]; then
    printf 'User runtime checksum mismatch: %s\n' "$1" >&2
    exit 2
  fi
}
stock_jar="$configured_home/vdbench.jar"
extension="$runtime_dir/@EXTENSION@"
require_sha "$stock_jar" '@STOCK_SHA@'
require_sha "$extension" '@EXTENSION_SHA@'
require_sha "$runtime_dir/runtime.json" '@META_SHA@'
require_sha "$runtime_dir/stock-home" '@HOME_SHA@'
unset JDK_JAVA_OPTIONS JAVA_TOOL_OPTIONS _JAVA_OPTIONS
if [[ -n ${JAVA_HOME:-} ]]; then
  java="$JAVA_HOME/bin/java"
elif [[ -x "$configured_home/jre/bin/java" ]]; then
  java="$configured_home/jre/bin/java"
else
  java=/usr/bin/java
fi
main=Vdb.Vdbmain
heap=${VDBENCH_MAIN_XMX:-512m}
if [[ ${1:-} == SlaveJvm ]]; then
  main=Vdb.SlaveJvm
  heap=${VDBENCH_SLAVE_XMX:-@SLAVE_HEAP@}
fi
cd -- "$runtime_dir"
opts=(-XX:-UsePerfData -Xms64m -Xmx"$heap")
if [[ -n ${TMPDIR:-} ]]; then
  opts+=("-Djava.io.tmpdir=$TMPDIR")
fi
exec "$java" "${opts[@]}" -cp "$stock_jar:$extension" "$main" "$@"
