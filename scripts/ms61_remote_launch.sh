#!/usr/bin/env bash
# Run ON THE NANCY FRONTEND (one ssh call from Kenzae): ms61_remote_launch.sh JOB_ID PLAN.json OUT_DIR LANES
# It only dispatches: it checks the OAR job, freezes the scripts and the plan in OUT_DIR, moves aside unfinished cells of
# finished jobs, then starts one detached runner per job host with oarsh (in parallel, each oarsh under a timeout) and
# writes a short report. Nothing heavy runs on the frontend. The runners themselves refuse frontends.
#
# The tools are staged next to this script (normally ~/ms61-bpc/final-scripts/scripts/, see docs/ms61-final-campaign.md).
# A relative OUT_DIR lives under ~/ms61-bpc/runs/. A relative PLAN is relative to the current directory.
# The dispatch runs detached (setsid nohup) and logs to OUT_DIR/runners/launch-<epoch>.log, so a dropped ssh session
# cannot leave a half-finished launch. The foreground waits up to MS61_LAUNCH_WAIT_S (default 240) and prints the report.
# Relaunching the same command resumes: cells with a cell.json are skipped by the runners, and a host that already has a
# live runner on OUT_DIR refuses the second one through its per-host flock.
#
# Environment:
#   MS61_RUNNER_PYTHON         runner interpreter (default ~/ms56-ablation/experiment/.venv/bin/python)
#   MS61_SHARD                 dynamic (default: every runner claims the next free cell) or static (host i of k: index % k == i)
#   MS61_STOP_LAUNCHING_AFTER  UTC ISO time after which runners start no new cell, 'none' to disable;
#                              default: job start + walltime - longest time limit - grace - MS61_STOP_MARGIN_S (default 600)
#   MS61_RUNNER_ARGS           extra runner flags, e.g. "--grace-s 20"
#   MS61_OARSH_TIMEOUT_S       timeout per oarsh dispatch (default 90)
#   MS61_FORCE_PLAN=1          replace a different plan.json already in OUT_DIR (the old one is kept as plan.replaced-<epoch>.json)
#   MS61_FORCE_SCRIPTS=1       replace different frozen scripts in OUT_DIR/scripts (the old copy is kept as scripts.replaced-<epoch>)
set -euo pipefail
usage='usage: ms61_remote_launch.sh JOB_ID PLAN.json OUT_DIR LANES'
job=${1:?$usage}
plan_arg=${2:?$usage}
out=${3:?$usage}
lanes=${4:?$usage}
[[ "$job" =~ ^[0-9]+$ && "$lanes" =~ ^[0-9]+$ ]] || { echo "JOB_ID and LANES must be integers" >&2; exit 2; }
command -v oarsh >/dev/null && command -v oarstat >/dev/null || { echo "oarsh/oarstat not found: run this on the G5K frontend" >&2; exit 2; }
case "$(hostname -s)" in
    f*) ;;
    *) echo "Refusing: ms61_remote_launch.sh dispatches from the frontend, not from $(hostname -s)" >&2; exit 2 ;;
esac
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ "$out" = /* ]] || out="$HOME/ms61-bpc/runs/$out"
plan=$(realpath "$plan_arg")
[[ -f "$plan" ]] || { echo "No plan at $plan" >&2; exit 2; }
mkdir -p "$out/runners"

if [[ "${MS61_LAUNCH_DETACHED:-0}" != 1 ]]; then
    stamp=$(date +%s)
    log="$out/runners/launch-$stamp.log"
    MS61_LAUNCH_DETACHED=1 MS61_LAUNCH_LOG="$log" setsid nohup bash "${BASH_SOURCE[0]}" "$job" "$plan" "$out" "$lanes" >"$log" 2>&1 </dev/null &
    echo "Dispatch detached (pid $!), log $log"
    wait_s=${MS61_LAUNCH_WAIT_S:-240}
    for ((i = 0; i < wait_s; i += 2)); do
        if grep -q '^LAUNCH \(COMPLETE\|FAILED\)' "$log" 2>/dev/null; then break; fi
        sleep 2
    done
    cat "$log"
    grep -q '^LAUNCH COMPLETE' "$log" || { echo "(launch report not complete yet: ms61_status.sh shows it later)"; exit 1; }
    exit 0
fi

trap 'echo "LAUNCH FAILED (line $LINENO)"' ERR
t0=$(date +%s.%N)
echo "ms61 remote launch $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname -s): job $job, plan $plan, out $out, $lanes lanes per host"
python=${MS61_RUNNER_PYTHON:-$HOME/ms56-ablation/experiment/.venv/bin/python}
shard_mode=${MS61_SHARD:-dynamic}
extra=${MS61_RUNNER_ARGS:-}
oarsh_timeout=${MS61_OARSH_TIMEOUT_S:-90}
[[ -x "$python" ]] || { echo "Runner python $python not found"; echo "LAUNCH FAILED"; exit 3; }

# Job state, hosts and launch window, from the JSON form of oarstat (epochs, no local-time parsing).
job_info=$(oarstat -fJ -j "$job" | python3 -c '
import json, sys
job = next(iter(json.load(sys.stdin).values()))
hosts = sorted({h.split(".")[0] for h in job.get("assigned_network_address") or []}, key=lambda h: (h.rsplit("-", 1)[0], int(h.rsplit("-", 1)[1]) if h.rsplit("-", 1)[-1].isdigit() else 0))
print(job["state"], job.get("start_time") or 0, job.get("walltime") or 0, " ".join(hosts))')
read -r state start_time walltime hosts_line <<<"$job_info"
[[ "$state" == Running ]] || { echo "Job $job is $state, not Running"; echo "LAUNCH FAILED"; exit 3; }
read -r -a hosts <<<"$hosts_line"
(( ${#hosts[@]} > 0 )) || { echo "No assigned hosts for job $job"; echo "LAUNCH FAILED"; exit 3; }
read -r ncells max_limit < <(python3 -c '
import json, sys
raw = json.load(open(sys.argv[1]))
defaults, cells = ({}, raw) if isinstance(raw, list) else (raw.get("defaults", {}), raw["cells"])
print(len(cells), max(float({**defaults, **c}["time_limit_s"]) for c in cells))' "$plan")
grace=$(sed -n 's/.*--grace-s[ =]\([0-9.]*\).*/\1/p' <<<"$extra"); grace=${grace:-10}
stop=${MS61_STOP_LAUNCHING_AFTER:-}
if [[ -z "$stop" ]]; then
    stop_epoch=$(python3 -c 'import sys; a = [float(x) for x in sys.argv[1:]]; print(int(a[0] + a[1] - a[2] - a[3] - a[4]))' \
        "$start_time" "$walltime" "$max_limit" "$grace" "${MS61_STOP_MARGIN_S:-600}")
    stop=$(date -u -d "@$stop_epoch" +%Y-%m-%dT%H:%M:%SZ)
    if (( stop_epoch < $(date +%s) )); then
        echo "Launch window already closed ($stop): job end $(date -u -d "@$((start_time + walltime))" +%H:%M:%SZ) leaves no room for a ${max_limit} s cell"
        echo "LAUNCH FAILED"; exit 3
    fi
fi
stop_flag=()
[[ "$stop" != none ]] && stop_flag=(--stop-launching-after "$stop")
echo "job $job Running on ${#hosts[@]} hosts, walltime ${walltime} s from $(date -u -d "@$start_time" +%Y-%m-%dT%H:%M:%SZ), plan $ncells cells, longest limit ${max_limit} s, no new cell after $stop"

# Frozen scripts and plan.
files=(ms61_run_cells.py ms61_kayros_worker.py ms61_lera_worker.py ms61_build.sh ms61_tree_hash.sh checker_contract.py
       ms61_final_plan.py ms61_remote_launch.sh ms61_status.sh)
mkdir -p "$out/scripts" "$out/cells"
same=1
for f in "${files[@]}"; do
    [[ -f "$script_dir/$f" ]] || { echo "Missing staged script $script_dir/$f"; echo "LAUNCH FAILED"; exit 3; }
    if [[ -e "$out/scripts/$f" ]] && ! cmp -s "$script_dir/$f" "$out/scripts/$f"; then same=0; fi
done
if [[ $same == 0 ]]; then
    [[ "${MS61_FORCE_SCRIPTS:-0}" == 1 ]] || { echo "$out/scripts differs from $script_dir (set MS61_FORCE_SCRIPTS=1 to replace)"; echo "LAUNCH FAILED"; exit 4; }
    mv "$out/scripts" "$out/scripts.replaced-$(date +%s)"
    mkdir -p "$out/scripts"
fi
for f in "${files[@]}"; do [[ -e "$out/scripts/$f" ]] || cp -p "$script_dir/$f" "$out/scripts/$f"; done
if [[ -e "$out/plan.json" ]] && ! cmp -s "$plan" "$out/plan.json"; then
    [[ "${MS61_FORCE_PLAN:-0}" == 1 ]] || { echo "$out/plan.json differs from $plan (set MS61_FORCE_PLAN=1 to replace)"; echo "LAUNCH FAILED"; exit 4; }
    mv "$out/plan.json" "$out/plan.replaced-$(date +%s).json"
fi
[[ -e "$out/plan.json" ]] || cp "$plan" "$out/plan.json"
sha256sum "$out/plan.json" "${files[@]/#/$out/scripts/}" | sed 's|  .*/|  |'

# Unfinished cells of jobs that no longer run are moved aside here, where oarstat is available.
python3 - "$out" "$job" <<'PY'
import json, subprocess, sys, time
from pathlib import Path
out, job = Path(sys.argv[1]), sys.argv[2]
states, moved, live = {}, 0, 0
for d in sorted((out / 'cells').iterdir()):
    if not d.is_dir() or any(t in d.name for t in ('.stale-', '.failed-', '.orphan-')) or (d / 'cell.json').exists() or not (d / 'claim.json').exists():
        continue
    owner = json.loads((d / 'claim.json').read_bytes()).get('oar_job_id')
    if owner == job or not owner:
        live += 1
        continue
    if owner not in states:
        r = subprocess.run(['oarstat', '-s', '-j', str(owner)], capture_output=True, text=True)
        states[owner] = r.stdout.strip().rsplit(':', 1)[-1].strip() if r.returncode == 0 else None
    if states[owner] in ('Terminated', 'Error'):
        d.rename(d.with_name(f'{d.name}.stale-{int(time.time())}'))
        moved += 1
    else:
        live += 1
print(f'unfinished cells: {moved} of finished jobs moved aside, {live} kept (current or unknown job) {states}')
PY

# One detached runner per host, dispatched in parallel.
rm -f "$out"/runners/dispatch-*.txt
count=${#hosts[@]}
for i in "${!hosts[@]}"; do
    host=${hosts[$i]}
    shard=dynamic
    [[ "$shard_mode" == static ]] && shard="$i/$count"
    starter="$out/runners/start-$host.sh"
    cat >"$starter" <<EOF
#!/usr/bin/env bash
cd '$out'
export OAR_JOB_ID=$job
setsid nohup '$python' '$out/scripts/ms61_run_cells.py' --plan '$out/plan.json' --out '$out' --jobs $lanes --shard $shard ${stop_flag[*]} $extra >> '$out/runners/$host.log' 2>&1 < /dev/null &
echo "started runner pid \$! on \$(hostname -s)"
EOF
    (
        s=$(date +%s.%N)
        msg=$(OAR_JOB_ID=$job timeout "$oarsh_timeout" oarsh "$host" "bash '$starter'" </dev/null 2>&1) && rc=0 || rc=$?
        e=$(date +%s.%N)
        printf '%s rc=%s dispatch_s=%.2f %s\n' "$host" "$rc" "$(python3 -c "print($e - $s)")" "$(tr '\n' ' ' <<<"$msg")" >"$out/runners/dispatch-$host.txt"
    ) &
done
wait
t1=$(date +%s.%N)
sleep 12
echo
echo "host               dispatch                         runner record                       log tail"
for host in "${hosts[@]}"; do
    d=$(cat "$out/runners/dispatch-$host.txt" 2>/dev/null || echo "$host no dispatch record")
    rec=$(python3 -c '
import json, sys
try:
    r = json.load(open(sys.argv[1])); print("pid %s %s %d lanes" % (r["pid"], r["started_utc"][11:19], len(r["lanes"])))
except Exception as e:
    print("no runner json")' "$out/runners/$host.json")
    tailline=$(tail -n 1 "$out/runners/$host.log" 2>/dev/null | cut -c1-110 || true)
    printf '%-18s %-32s %-35s %s\n' "$host" "$(cut -d' ' -f2-3 <<<"$d")" "$rec" "$tailline"
done
python3 -c "print(f'dispatch wall time {float(\"$t1\") - float(\"$t0\"):.1f} s for $count hosts (parallel oarsh)')"
failed=$(grep -L ' rc=0 ' "$out"/runners/dispatch-*.txt 2>/dev/null | wc -l)
echo "Monitor from Kenzae: bash scripts/ms61_status.sh $(basename "$out")"
if (( failed )); then echo "LAUNCH COMPLETE with $failed failed dispatches (rerun the same command to retry them)"; else echo "LAUNCH COMPLETE"; fi
