#!/usr/bin/env bash
set -euo pipefail
variant=${1:?source variant}
backend=${2:?cplex or highs}
case "$variant" in best|scratch|safe-correction) ;; *) exit 2 ;; esac
case "$backend" in cplex|highs) ;; *) exit 2 ;; esac
test -n "${OAR_JOB_ID:?}"
case "$(hostname -s)" in grvingt-*|grappe-*|grdix-*|gros-*) ;; *) exit 3 ;; esac
root="$HOME/ms58-bpca"
run="$root/dabia-20261110"
export TMPDIR="$root/tmp"
export CMAKE_BUILD_PARALLEL_LEVEL=8
export CPLEX_INCLUDE="$HOME/cplex1210/cplex/include"
export CPLEX_BIN="$HOME/cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a"
module load boost
cd "$root/sources/$variant"
mkdir -p "$root/artifacts/$variant-$backend" "$run/build-logs"
test ! -f "$run/build-logs/$variant-$backend.complete"
{
    date -Is
    hostname
    printf 'OAR_JOB_ID=%s\n' "$OAR_JOB_ID"
    c++ --version
    module list
    sha256sum "$CPLEX_BIN"
    "$HOME/.local/bin/uv" build --python 3.13 --wheel . \
        --out-dir "$root/artifacts/$variant-$backend" \
        --config-setting=cmake.define.KAYROS_WITH_LERA=ON \
        --config-setting="cmake.define.LERA_LP_BACKEND=$backend" \
        --config-setting="build-dir=build/$backend"
    "$HOME/.local/bin/uv" venv --python 3.13 "$root/venv-$variant-$backend"
    "$HOME/.local/bin/uv" pip install --python "$root/venv-$variant-$backend/bin/python" \
        -r "$HOME/ms56-phase2/solver/requirements-checker.txt" \
        "$root/artifacts/$variant-$backend"/*.whl pytest
    "$root/venv-$variant-$backend/bin/python" -c \
        'import sys,kayros; from kayros import _lera; print(kayros.__version__, _lera.LP_BACKEND); assert _lera.LP_BACKEND.casefold() == sys.argv[1]' "$backend"
    sha256sum "$root/artifacts/$variant-$backend"/*.whl \
        "$root/venv-$variant-$backend"/lib/python3.13/site-packages/kayros/*.so
    date -Is
} >"$run/build-logs/$variant-$backend.log" 2>&1
touch "$run/build-logs/$variant-$backend.complete"
