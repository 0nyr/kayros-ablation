#!/usr/bin/env bash
set -euo pipefail
variant=${1:?reference, normalize, or diagnostics}
case "$variant" in reference|normalize|diagnostics|replay|point-addition|task-replay|point-replay|stable-branch|canonical-normalize) ;; *) exit 2 ;; esac
test -n "${OAR_JOB_ID:-}"
case "$(hostname -s)" in grvingt-*|grappe-*|grdix-*|gros-*) ;; *) exit 3 ;; esac
root="$HOME/ms58-bpca"
export TMPDIR="$root/tmp"
export CMAKE_BUILD_PARALLEL_LEVEL=8
export CPLEX_INCLUDE="$HOME/cplex1210/cplex/include"
export CPLEX_BIN="$HOME/cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a"
module load boost
cd "$root/sources/$variant"
mkdir -p "$root/artifacts/$variant"
{
    date -Is
    hostname
    printf 'OAR_JOB_ID=%s\n' "$OAR_JOB_ID"
    c++ --version
    module list
    sha256sum "$CPLEX_BIN"
    "$HOME/.local/bin/uv" build --python 3.13 --wheel . --out-dir "$root/artifacts/$variant" \
        --config-setting=cmake.define.KAYROS_WITH_LERA=ON \
        --config-setting=cmake.define.LERA_LP_BACKEND=cplex \
        --config-setting=build-dir=build/cplex
    "$HOME/.local/bin/uv" venv --python 3.13 "$root/venv-$variant"
    "$HOME/.local/bin/uv" pip install --python "$root/venv-$variant/bin/python" \
        -r "$HOME/ms56-phase2/solver/requirements-checker.txt" \
        "$root/artifacts/$variant"/*.whl pytest
    "$root/venv-$variant/bin/python" -c 'import kayros; from kayros import _lera; print(kayros.__version__, _lera.LP_BACKEND)'
    sha256sum "$root/artifacts/$variant"/*.whl "$root/venv-$variant"/lib/python3.13/site-packages/kayros/*.so
    date -Is
} >"$root/logs/build-$variant.log" 2>&1
touch "$root/logs/build-$variant.complete"
