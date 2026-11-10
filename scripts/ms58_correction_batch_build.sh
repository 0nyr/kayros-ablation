#!/usr/bin/env bash
set -euo pipefail
root=${1:?isolated staged correction-domain-batch root}
control_root=${2:?existing ms58-bpca root containing immutable correction-on control}
job_id=${3:?declared OAR job ID}
core=${4:?allowed physical CPU for build sealing}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
test "${OAR_JOB_ID:?}" = "$job_id"
case "$(hostname -s)" in grvingt-*|grappe-*|grdix-*|gros-*) ;; *) exit 3 ;; esac
test -f "${OAR_NODEFILE:?}"
export TMPDIR="$root/tmp-build"
export CMAKE_BUILD_PARALLEL_LEVEL=4
export CPLEX_INCLUDE="$HOME/cplex1210/cplex/include"
export CPLEX_BIN="$HOME/cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a"
module load boost
mkdir -p "$TMPDIR" "$root/artifacts/correction-on-domain-batch-cplex"
log="$root/build.log"
test ! -e "$log"
test ! -e "$root/build-complete.json"
exec >"$log" 2>&1
python3 - "$script_dir" "$root" "$control_root" "$job_id" "$core" <<'PY'
import sys
from pathlib import Path
scripts, root, control_root, job, core = sys.argv[1:]
sys.path.insert(0, scripts)
from ms58_correction_batch_native import allocation, verify_sources
allocation(job, [int(core)], 32)
verify_sources(Path(root), Path(root) / 'source-inventory.json', Path(control_root))
PY
date -Is
hostname
c++ --version
module list
sha256sum "$CPLEX_BIN"
cd "$root/sources/correction-on-domain-batch"
"$HOME/.local/bin/uv" build --python 3.13 --wheel . \
    --out-dir "$root/artifacts/correction-on-domain-batch-cplex" \
    --config-setting=cmake.define.KAYROS_WITH_LERA=ON \
    --config-setting=cmake.define.LERA_LP_BACKEND=cplex \
    --config-setting=cmake.define.KAYROS_EXTENSION_PROFILING=OFF \
    --config-setting=cmake.define.CMAKE_EXPORT_COMPILE_COMMANDS=ON \
    --config-setting=build-dir=build/cplex
"$HOME/.local/bin/uv" venv --python 3.13 "$root/venv-correction-on-domain-batch-cplex"
"$HOME/.local/bin/uv" pip install --python "$root/venv-correction-on-domain-batch-cplex/bin/python" \
    -r "$HOME/ms56-phase2/solver/requirements-checker.txt" \
    "$root/artifacts/correction-on-domain-batch-cplex"/*.whl
"$HOME/.local/bin/uv" venv --python 3.13 "$root/venv-build-tools"
"$HOME/.local/bin/uv" pip install --python "$root/venv-build-tools/bin/python" ninja==1.13.0
"$control_root/venv-correction-on-cplex/bin/python" "$script_dir/ms58_correction_batch_native.py" \
    --mode seal-build --root "$root" --control-root "$control_root" --job-id "$job_id" --core "$core" \
    --source-identity "$root/source-inventory.json" --output "$root/build-complete.json"
date -Is
printf 'BUILD_ONLY_NO_NATIVE_OR_SOLVER_LAUNCH\n'
