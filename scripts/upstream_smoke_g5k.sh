#!/usr/bin/env bash
set -euo pipefail
: "${OAR_JOB_ID:?An owned OAR allocation is required}"
case "$(hostname -s)" in fnancy*|flyon*) exit 2 ;; esac
module load boost
smoke_root="$HOME/ms56-ablation/solver"
smoke_size="${1:-25}"
smoke_instance="C101_$smoke_size"
smoke_out="$smoke_root/upstream-probe-affinity-n$smoke_size"
mkdir -p "$smoke_out"
smoke_cpu=$(python3 -c 'import os; print(min(os.sched_getaffinity(0)))')
printf '%s\n' "$smoke_cpu" > "$smoke_out/cpu.txt"
timeout --signal=TERM --kill-after=2s 20s taskset -c "$smoke_cpu" python3 "$smoke_root/upstream_probe_worker.py" --binary "$smoke_root/upstream-build/bp" --source-root "$smoke_root/upstream" --instance "$smoke_instance" --seconds 2 > "$smoke_out/$smoke_instance.events.jsonl" 2> "$smoke_out/$smoke_instance.stderr"
sha256sum "$smoke_root/upstream-build/bp" "$HOME/cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a" > "$smoke_out/binary-library.sha256"
