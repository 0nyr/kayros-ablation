#!/usr/bin/env bash
set -euo pipefail
root=${1:?isolated reserved-compute root}
source_tree=${2:?staged a2dc source directory}
source_pins=${3:?source-pins JSON}
job_id=${4:?declared OAR job ID}
test "${OAR_JOB_ID:?}" = "$job_id"
case "$(hostname -s)" in gros-*) ;; *) exit 3 ;; esac
test -f "${OAR_NODEFILE:?}"
export TMPDIR="$root/tmp-dominance-profile"
export CMAKE_BUILD_PARALLEL_LEVEL=4
export CPLEX_INCLUDE="${CPLEX_INCLUDE:-$HOME/cplex1210/cplex/include}"
export CPLEX_BIN="${CPLEX_BIN:-$HOME/cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a}"
module load boost
mkdir -p "$TMPDIR" "$root/artifacts/dominance-profile-cplex" "$root/dominance-build-logs"
log="$root/dominance-build-logs/build.log"
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
identity=json.loads((root/'.dominance-source-identity.json').read_bytes())
assert identity['source_id']==pins['source_id']=='a2dcf8870c17e7edeccb472b27bee0e544eec997'
assert identity['source_pins_sha256']==hashlib.sha256(pins_path.read_bytes()).hexdigest()
for relative,expected in pins['files'].items():
    assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==expected,relative
for path,expected in identity['patches'].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected,path
PY
cd "$source_tree"
"$HOME/.local/bin/uv" build --python 3.13 --wheel . \
    --out-dir "$root/artifacts/dominance-profile-cplex" \
    --config-setting=cmake.define.KAYROS_WITH_LERA=ON \
    --config-setting=cmake.define.LERA_LP_BACKEND=cplex \
    --config-setting=cmake.define.KAYROS_DOMINANCE_PROFILING=ON \
    --config-setting=cmake.define.CMAKE_EXPORT_COMPILE_COMMANDS=ON \
    --config-setting=build-dir=build/cplex
"$HOME/.local/bin/uv" venv --python 3.13 "$root/venv-dominance-profile-cplex"
"$HOME/.local/bin/uv" pip install --python "$root/venv-dominance-profile-cplex/bin/python" \
    -r "$HOME/ms56-phase2/solver/requirements-checker.txt" \
    "$root/artifacts/dominance-profile-cplex"/*.whl
"$root/venv-dominance-profile-cplex/bin/python" -c \
    'from kayros import _lera;print(_lera.LP_BACKEND,_lera.__file__);assert _lera.LP_BACKEND=="CPLEX"'
sha256sum "$root/artifacts/dominance-profile-cplex"/*.whl \
    "$root/venv-dominance-profile-cplex"/lib/python3.13/site-packages/kayros/*.so \
    "$source_tree/build/cplex/cpp/lera/liblera.a"
date -Is
printf 'BUILD_ONLY_NO_NATIVE_OR_SOLVER_LAUNCH\n'
