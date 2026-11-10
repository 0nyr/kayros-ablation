#!/usr/bin/env bash
# Run on Kenzae: ms61_launch.sh JOB_ID PLAN.json OUT_DIR JOBS
# OUT_DIR is remote. A relative OUT_DIR lives under ~/ms61-bpc/runs/.
# Copies the ms61 scripts (to ~/ms61-bpc/scripts and a frozen copy in OUT_DIR/scripts) and the plan (OUT_DIR/plan.json),
# then starts one detached runner per host of the job, shard i/k round-robin over the plan cells.
# Environment: MS61_RUNNER_PYTHON (default ~/ms56-ablation/experiment/.venv/bin/python), MS61_RUNNER_ARGS (extra runner flags, e.g. "--grace-s 20"),
# MS61_FORCE_PLAN=1 to replace a different plan already present in OUT_DIR.
set -euo pipefail
job=${1:?usage: ms61_launch.sh JOB_ID PLAN.json OUT_DIR JOBS}
plan=$(realpath "${2:?usage: ms61_launch.sh JOB_ID PLAN.json OUT_DIR JOBS}")
out=${3:?usage: ms61_launch.sh JOB_ID PLAN.json OUT_DIR JOBS}
jobs=${4:?usage: ms61_launch.sh JOB_ID PLAN.json OUT_DIR JOBS}
[[ "$job" =~ ^[0-9]+$ && "$jobs" =~ ^[0-9]+$ ]] || { echo "JOB_ID and JOBS must be integers" >&2; exit 2; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
remote=nancy.g5k
ssh_opts=(-S none -o ConnectTimeout=15)
rsh="ssh ${ssh_opts[*]}"
remote_home=$(ssh "${ssh_opts[@]}" "$remote" 'echo $HOME')
[[ "$out" = /* ]] || out="$remote_home/ms61-bpc/runs/$out"
python=${MS61_RUNNER_PYTHON:-$remote_home/ms56-ablation/experiment/.venv/bin/python}
extra=${MS61_RUNNER_ARGS:-}
ncells=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(len(d if isinstance(d, list) else d["cells"]))' "$plan") || { echo "Plan is not valid JSON" >&2; exit 2; }

state=$(ssh "${ssh_opts[@]}" "$remote" "oarstat -fj $job" )
grep -q 'state = Running' <<<"$state" || { echo "Job $job is not Running" >&2; grep -E 'state =' <<<"$state" >&2 || true; exit 3; }
mapfile -t hosts < <(sed -n 's/^ *assigned_hostnames = //p' <<<"$state" | tr '+' '\n' | sed 's/\..*//' | sed '/^$/d')
(( ${#hosts[@]} > 0 )) || { echo "No assigned hosts found for job $job" >&2; exit 3; }

files=(ms61_run_cells.py ms61_kayros_worker.py ms61_lera_worker.py ms61_build.sh ms61_tree_hash.sh checker_contract.py)
ssh "${ssh_opts[@]}" "$remote" "mkdir -p ms61-bpc/scripts '$out/scripts' '$out/runners'"
rsync -a -e "$rsh" "${files[@]/#/$script_dir/}" "$remote:ms61-bpc/scripts/"
rsync -a -e "$rsh" "${files[@]/#/$script_dir/}" "$remote:$out/scripts/"
if ssh "${ssh_opts[@]}" "$remote" "test -e '$out/plan.json'"; then
    if ! ssh "${ssh_opts[@]}" "$remote" "cat '$out/plan.json'" | cmp -s - "$plan"; then
        [[ "${MS61_FORCE_PLAN:-0}" == 1 ]] || { echo "$out/plan.json differs from $plan (set MS61_FORCE_PLAN=1 to replace)" >&2; exit 4; }
        ssh "${ssh_opts[@]}" "$remote" "mv '$out/plan.json' '$out/plan.replaced-$(date +%s).json'"
    fi
fi
rsync -a -e "$rsh" "$plan" "$remote:$out/plan.json"

count=${#hosts[@]}
for i in "${!hosts[@]}"; do
    host=${hosts[$i]}
    starter="$out/runners/start-$host.sh"
    ssh "${ssh_opts[@]}" "$remote" "cat > '$starter'" <<EOF
#!/usr/bin/env bash
cd '$out'
setsid nohup '$python' '$out/scripts/ms61_run_cells.py' --plan '$out/plan.json' --out '$out' --jobs $jobs --shard $i/$count $extra >> '$out/runners/$host.log' 2>&1 < /dev/null &
echo "started runner pid \$! on \$(hostname -s)"
EOF
    ssh "${ssh_opts[@]}" "$remote" "OAR_JOB_ID=$job oarsh $host 'bash $starter'" < /dev/null
    sleep 3
    ssh "${ssh_opts[@]}" "$remote" "tail -n 3 '$out/runners/$host.log' 2>/dev/null || echo 'no runner log yet on $host'"
done

cat <<EOF

Launched job $job on ${count} host(s): ${hosts[*]} with $jobs lanes each.
Monitor:
  ssh nancy.g5k 'echo \$(ls $out/cells/*/cell.json 2>/dev/null | wc -l) of $ncells cells done; tail -n 4 $out/runners/*.log'
Rsync back (when done):
  rsync -a nancy.g5k:$out/ ${script_dir%/scripts}/outputs/ms61/$(basename "$out")/
Summarize:
  cd ${script_dir%/scripts} && nix develop ../nix-dev --command python analysis/ms61_summary.py outputs/ms61/$(basename "$out")
EOF
