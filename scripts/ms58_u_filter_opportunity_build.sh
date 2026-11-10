#!/usr/bin/env bash
set -euo pipefail
root=${1:?isolated reserved-compute root}
source_tree=${2:?staged U-opportunity source directory}
source_pins=${3:?source-pins JSON}
job_id=${4:?declared OAR job ID}
test "${OAR_JOB_ID:?}" = "$job_id"
case "$(hostname -s)" in gros-*) ;; *) exit 3 ;; esac
test -f "${OAR_NODEFILE:?}"
export TMPDIR="$root/tmp-u-filter-opportunity"
export CMAKE_BUILD_PARALLEL_LEVEL=4
export CPLEX_INCLUDE="${CPLEX_INCLUDE:-$HOME/cplex1210/cplex/include}"
export CPLEX_BIN="${CPLEX_BIN:-$HOME/cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a}"
module load boost
mkdir -p "$TMPDIR" "$root/artifacts/u-filter-opportunity-cplex" "$root/u-filter-build-logs"
log="$root/u-filter-build-logs/build.log"
test ! -e "$log"
exec >"$log" 2>&1
date -Is
hostname
printf 'OAR_JOB_ID=%s\n' "$OAR_JOB_ID"
c++ --version
module list
sha256sum "$CPLEX_BIN"
python3 - "$source_tree" "$source_pins" <<'PY'
import hashlib,json,os,socket,sys
from pathlib import Path
root,pins_path=map(Path,sys.argv[1:])
assert not any(k.startswith(('KAYROS_','UPSTREAM_','PROBE_')) for k in os.environ)
assert socket.gethostname().split('.')[0] in {h.split('.')[0] for h in Path(os.environ['OAR_NODEFILE']).read_text().split()}
pins=json.loads(pins_path.read_bytes())
identity=json.loads((root/'.u-filter-opportunity-source-identity.json').read_bytes())
assert identity['source_id']==pins['source_id']=='7d68859e8d6d8b8ae2382c1f7a88088709f099b1'
assert identity['source_pins_sha256']==hashlib.sha256(pins_path.read_bytes()).hexdigest()
for relative,expected in pins['files'].items():
    assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==expected,relative
for path,expected in identity['patches'].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected,path
PY
cd "$source_tree"
"$HOME/.local/bin/uv" build --python 3.13 --wheel . \
    --out-dir "$root/artifacts/u-filter-opportunity-cplex" \
    --config-setting=cmake.define.KAYROS_WITH_LERA=ON \
    --config-setting=cmake.define.LERA_LP_BACKEND=cplex \
    --config-setting=cmake.define.KAYROS_DOMINANCE_PROFILING=ON \
    --config-setting=cmake.define.KAYROS_U_FILTER_OPPORTUNITY_PROFILING=ON \
    --config-setting=cmake.define.CMAKE_EXPORT_COMPILE_COMMANDS=ON \
    --config-setting=build-dir=build/cplex
"$HOME/.local/bin/uv" venv --python 3.13 "$root/venv-u-filter-opportunity-cplex"
"$HOME/.local/bin/uv" pip install --python "$root/venv-u-filter-opportunity-cplex/bin/python" \
    -r "$HOME/ms56-phase2/solver/requirements-checker.txt" \
    "$root/artifacts/u-filter-opportunity-cplex"/*.whl
"$root/venv-u-filter-opportunity-cplex/bin/python" -c \
    'from kayros import _lera;print(_lera.LP_BACKEND,_lera.__file__);assert _lera.LP_BACKEND=="CPLEX"'
sha256sum "$root/artifacts/u-filter-opportunity-cplex"/*.whl \
    "$root/venv-u-filter-opportunity-cplex"/lib/python3.13/site-packages/kayros/*.so \
    "$source_tree/build/cplex/cpp/lera/liblera.a"
date -Is
printf 'BUILD_ONLY_NO_NATIVE_OR_SOLVER_LAUNCH\n'
