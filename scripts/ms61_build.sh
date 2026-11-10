#!/usr/bin/env bash
# Run ON A G5K COMPUTE NODE inside a job, e.g.
#   ssh nancy.g5k "OAR_JOB_ID=<job> oarsh <node> 'bash -l ~/ms61-bpc/scripts/ms61_build.sh ~/ms61-bpc/sources/<name> ~/ms61-bpc/venvs/<name>'"
# Builds the Kayros CPLEX wheel (Lera bridge, CPLEX 12.10, same cmake defines as ms58_correction_batch_build.sh), then creates a fresh venv with the pinned checker requirements and that wheel.
# Side files next to the venv: <venv>.wheel/ (wheel), <venv>.build.log and <venv>.build-info.json.
set -euo pipefail
src=$(realpath "${1:?usage: ms61_build.sh SOURCE_DIR VENV_DIR}")
venv=$(realpath -m "${2:?usage: ms61_build.sh SOURCE_DIR VENV_DIR}")
case "$(hostname -s)" in
    grvingt-*|grappe-*|grdix-*|gros-*|paradoxe-*) ;;
    *) echo "Refusing to build on $(hostname -s): run on a reserved compute node" >&2; exit 3 ;;
esac
[[ -e "$venv" ]] && { echo "Venv $venv already exists; choose a new name or remove it first" >&2; exit 4; }
[[ -d "$src/cpp" && -f "$src/pyproject.toml" ]] || { echo "$src is not a Kayros source tree" >&2; exit 5; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$HOME/ms61-bpc
uv=$HOME/.local/bin/uv
wheel_dir="$venv.wheel"
log="$venv.build.log"
info="$venv.build-info.json"
mkdir -p "$(dirname "$venv")" "$root/tmp"
export TMPDIR
TMPDIR=$(mktemp -d "$root/tmp/build-XXXXXX")
export CMAKE_BUILD_PARALLEL_LEVEL=${MS61_BUILD_JOBS:-8}
export CPLEX_INCLUDE="$HOME/cplex1210/cplex/include"
export CPLEX_BIN="$HOME/cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a"
if ! type module >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    source /etc/profile >/dev/null 2>&1 || true
fi
module load boost
start_epoch=$(date +%s)
{
    date -Is
    hostname
    c++ --version
    module list 2>&1
    sha256sum "$CPLEX_BIN"
    [[ -f "$src/SOURCE_ID" ]] && cat "$src/SOURCE_ID"
    rm -rf "$wheel_dir"
    # Always build from scratch: rsync keeps source mtimes, so an incremental build could miss changed files.
    rm -rf "$src/build/cplex"
    cd "$src"
    "$uv" build --python 3.13 --wheel . \
        --out-dir "$wheel_dir" \
        --config-setting=cmake.define.KAYROS_WITH_LERA=ON \
        --config-setting=cmake.define.LERA_LP_BACKEND=cplex \
        --config-setting=cmake.define.KAYROS_EXTENSION_PROFILING=OFF \
        --config-setting=cmake.define.CMAKE_EXPORT_COMPILE_COMMANDS=ON \
        --config-setting=build-dir=build/cplex
    "$uv" venv --python 3.13 "$venv"
    "$uv" pip install --python "$venv/bin/python" \
        -r "$HOME/ms56-phase2/solver/requirements-checker.txt" \
        "$wheel_dir"/*.whl
    date -Is
} >"$log" 2>&1 || { echo "Build failed, see $log" >&2; tail -30 "$log" >&2; exit 1; }
end_epoch=$(date +%s)
content_hash=$("$script_dir/ms61_tree_hash.sh" "$src")
"$venv/bin/python" - "$src" "$venv" "$info" "$content_hash" "$start_epoch" "$end_epoch" "$CPLEX_BIN" <<'PY'
import hashlib, json, os, socket, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
src, venv, info, content_hash, start, end, cplex = sys.argv[1:]
def sha(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
package = next(Path(venv).glob('lib/python3*/site-packages/kayros'))
source_id = Path(src, 'SOURCE_ID')
record = {
    'source_dir': src, 'venv': venv, 'host': socket.gethostname(), 'oar_job_id': os.environ.get('OAR_JOB_ID'),
    'date_utc': datetime.now(timezone.utc).isoformat(), 'build_seconds': int(end) - int(start),
    'content_sha256_cpp_src': content_hash,
    'source_id_file': source_id.read_text() if source_id.exists() else None,
    'lera_so_sha256': {p.name: sha(p) for p in sorted(package.glob('_lera*.so'))},
    'core_so_sha256': {p.name: sha(p) for p in sorted(package.glob('_core*.so'))},
    'wheel_sha256': {p.name: sha(p) for p in sorted(Path(venv + '.wheel').glob('*.whl'))},
    'compiler_version': subprocess.run(['c++', '--version'], capture_output=True, text=True).stdout.splitlines()[0],
    'cplex_static_library_sha256': sha(cplex),
    'cmake_defines': {'KAYROS_WITH_LERA': 'ON', 'LERA_LP_BACKEND': 'cplex', 'KAYROS_EXTENSION_PROFILING': 'OFF',
                      'CMAKE_EXPORT_COMPILE_COMMANDS': 'ON', 'build-dir': 'build/cplex'},
}
tmp = info + '.tmp'
Path(tmp).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
os.replace(tmp, info)
print(json.dumps(record, indent=2, sort_keys=True))
PY
rm -rf "$TMPDIR"
echo "BUILD_OK $venv in $((end_epoch - start_epoch)) s"
