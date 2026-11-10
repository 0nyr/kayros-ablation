#!/usr/bin/env bash
set -euo pipefail
: "${OAR_JOB_ID:?}"
test "$OAR_JOB_ID" = 7013762
root="$HOME/ms58-bpca"
instance="$HOME/kayros-sweeps/MAMUT-routing/benchmarks/TDVRPTW/Vu2020/n=59/Vu-A2-pB-d98-w100.vrp.json"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
for direction in forward reverse; do
    if [[ "$direction" = forward ]]; then
        export KAYROS_TRACE_PATH=0,31,6,55,33,60
    else
        export KAYROS_TRACE_PATH=60,33,55,6,31,0
    fi
    export KAYROS_TRACE_MERGE=0,31,6,55,33,60 KAYROS_TRACE_DUMP=1
    export KAYROS_PRICING_TRACE="$root/resume-20261110/vu-path-$direction.jsonl"
    timeout 150 taskset -c 6 "$root/venv-diagnostics/bin/python" \
        "$root/experiment/scripts/ms58_vu_witness_probe.py" \
        --instance "$instance" --witness "${instance%.vrp.json}.bks.Duration.json" \
        --output "$root/resume-20261110/vu-path-$direction" --source-id eb94418 --horizon 120 \
        > "$root/resume-20261110/vu-path-$direction.log" 2>&1
done
