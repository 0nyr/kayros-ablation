#!/usr/bin/env bash
# Run inside an allocated compute node after preparing the pinned source tree.
set -euo pipefail
if [[ $# != 3 ]]; then
    echo 'usage: build_kayros_probe.sh SOURCE BACKEND OUTPUT_DIRECTORY' >&2
    exit 2
fi
probe_source=$(realpath "$1")
probe_backend=$2
probe_output=$(realpath -m "$3")
case "$probe_backend" in cplex|highs) ;; *) exit 2 ;; esac
mkdir -p "$probe_output"
: "${CMAKE_BUILD_PARALLEL_LEVEL:=8}"
export CMAKE_BUILD_PARALLEL_LEVEL
uv build --wheel "$probe_source" --out-dir "$probe_output" \
    --config-setting=cmake.define.KAYROS_WITH_LERA=ON \
    --config-setting=cmake.define.LERA_LP_BACKEND="$probe_backend" \
    --config-setting=build-dir="build/$probe_backend"
sha256sum "$probe_output"/*.whl
