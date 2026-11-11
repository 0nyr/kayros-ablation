#!/usr/bin/env bash
# Batch command of a campaign job, run by OAR ON THE FIRST NODE of the job:
#   oarsub -q abaca -p grvingt -n ms61-final-x -l host=N,walltime=H "bash ~/ms61-bpc/final-scripts/scripts/ms61_job_entry.sh RUN LANES"
# It starts one detached runner per job host (the same starter as ms61_remote_launch.sh), then waits until no runner is
# alive and exits, so the job releases its nodes by itself. Nothing depends on an ssh session from Kenzae at job start.
# RUN is a directory under ~/ms61-bpc/runs/ that already holds the frozen plan.json and scripts/ (prepared once from the
# frontend by ms61_remote_launch.sh or by hand). This script never copies or replaces them, it only checks they exist.
# Nodes have no oarstat, so the job window comes from OAR_JOB_WALLTIME_SECONDS and the start of this script.
# Unfinished cells of dead jobs are not moved aside here (that needs oarstat): rerun ms61_remote_launch.sh from the
# frontend for that case.
# Environment: MS61_STOP_MARGIN_S (default 600), MS61_ENTRY_MIN_S (default 1500: the job stays up at least this long,
# which leaves time for a manual launch if the dispatch failed), MS61_ENTRY_POLL_S (default 90), MS61_RUNNER_ARGS.
set -uo pipefail
usage='usage: ms61_job_entry.sh RUN LANES'
run=${1:?$usage}
lanes=${2:?$usage}
out=$run
[[ "$out" = /* ]] || out="$HOME/ms61-bpc/runs/$out"
job=${OAR_JOB_ID:-${OAR_JOBID:-}}
[[ -n "$job" ]] || { echo "No OAR job id in the environment" >&2; exit 2; }
case "$(hostname -s)" in
    grvingt-*|grappe-*|grdix-*|gros-*|paradoxe-*) ;;
    *) echo "Refusing: ms61_job_entry.sh runs on a job node, not on $(hostname -s)" >&2; exit 2 ;;
esac
start=$(date +%s)
wall=${OAR_JOB_WALLTIME_SECONDS:?no OAR_JOB_WALLTIME_SECONDS}
python=${MS61_RUNNER_PYTHON:-$HOME/ms56-ablation/experiment/.venv/bin/python}
extra=${MS61_RUNNER_ARGS:-}
[[ -f "$out/plan.json" && -f "$out/scripts/ms61_run_cells.py" ]] || { echo "No frozen plan or scripts in $out" >&2; exit 3; }
mkdir -p "$out/runners" "$out/cells"
log="$out/runners/entry-$job.log"
exec >>"$log" 2>&1
echo "ms61 job entry $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname -s): job $job, out $out, $lanes lanes per host, walltime $wall s"
mapfile -t hosts < <(sort -u "${OAR_NODEFILE:?no OAR_NODEFILE}" | sed 's/\..*//')
(( ${#hosts[@]} > 0 )) || { echo "No hosts in $OAR_NODEFILE"; exit 3; }
max_limit=$(python3 -c '
import json, sys
raw = json.load(open(sys.argv[1]))
defaults, cells = ({}, raw) if isinstance(raw, list) else (raw.get("defaults", {}), raw["cells"])
print(max(float({**defaults, **c}["time_limit_s"]) for c in cells))' "$out/plan.json")
grace=$(sed -n 's/.*--grace-s[ =]\([0-9.]*\).*/\1/p' <<<"$extra"); grace=${grace:-10}
stop_epoch=$(python3 -c 'import sys; a = [float(x) for x in sys.argv[1:]]; print(int(a[0] + a[1] - a[2] - a[3] - a[4]))' \
    "$start" "$wall" "$max_limit" "$grace" "${MS61_STOP_MARGIN_S:-600}")
stop=$(date -u -d "@$stop_epoch" +%Y-%m-%dT%H:%M:%SZ)
echo "hosts (${#hosts[@]}): ${hosts[*]}"
echo "longest limit $max_limit s, no new cell after $stop"
if (( stop_epoch <= start )); then
    echo "Launch window already closed: the walltime leaves no room for a $max_limit s cell"
    exit 3
fi
pattern="ms61_run_cells.py --plan $out/plan.json"
for host in "${hosts[@]}"; do
    starter="$out/runners/start-$host.sh"
    cat >"$starter" <<EOF
#!/usr/bin/env bash
cd '$out'
export OAR_JOB_ID=$job
setsid nohup '$python' '$out/scripts/ms61_run_cells.py' --plan '$out/plan.json' --out '$out' --jobs $lanes --shard dynamic --stop-launching-after $stop $extra >> '$out/runners/$host.log' 2>&1 < /dev/null &
echo "started runner pid \$! on \$(hostname -s)"
EOF
    (
        for attempt in 1 2 3; do
            msg=$(timeout 90 oarsh "$host" "bash '$starter'" </dev/null 2>&1) && rc=0 || rc=$?
            [[ $rc == 0 ]] && break
            sleep 5
        done
        printf '%s rc=%s job=%s %s\n' "$host" "$rc" "$job" "$(tr '\n' ' ' <<<"$msg")" >"$out/runners/dispatch-$host.txt"
    ) &
done
wait
cat "$out"/runners/dispatch-*.txt 2>/dev/null | grep " job=$job " || true
echo "dispatch done $(date -u +%H:%M:%SZ)"
min_s=${MS61_ENTRY_MIN_S:-1500}
poll=${MS61_ENTRY_POLL_S:-90}
idle=0
while true; do
    sleep "$poll"
    alive=0; unknown=0
    for host in "${hosts[@]}"; do
        n=$(timeout 60 oarsh "$host" "pgrep -f '$pattern' | wc -l" </dev/null 2>/dev/null) || n=unknown
        if [[ "$n" == unknown || -z "$n" ]]; then unknown=$((unknown + 1)); elif (( n > 0 )); then alive=$((alive + 1)); fi
    done
    now=$(date +%s)
    if (( alive == 0 && unknown == 0 )); then idle=$((idle + 1)); else idle=0; fi
    if (( (now - start) % 1800 < poll )); then echo "$(date -u +%H:%M:%SZ) runners alive on $alive hosts, unknown $unknown"; fi
    if (( idle >= 3 && now - start >= min_s )); then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) no runner alive on any host for $idle checks: leaving, the job ends"
        exit 0
    fi
done
