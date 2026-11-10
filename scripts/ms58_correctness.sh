#!/usr/bin/env bash
set -euo pipefail
: "${OAR_JOB_ID:?}"
root="$HOME/ms58-bpca"
cd "$root/sources/normalize"
c++ -std=c++23 -O3 -DNDEBUG -ffp-contract=off -Icpp/lera/goc/include -Icpp/lera/main/include \
    tests/cpp/test_label_normalization.cpp build/cplex/cpp/lera/liblera.a -ldl -lm \
    -o build/cplex/test_label_normalization
build/cplex/test_label_normalization > "$root/logs/normalization-correctness.log"
build/cplex/test_label_normalization --benchmark > "$root/outputs/normalization-benchmark.csv"
export MAMUT_ROUTING_BENCHMARKS_ROOT="$HOME/kayros-sweeps/MAMUT-routing/benchmarks"
"$root/venv-normalize/bin/python" -m pytest -q \
    tests/test_goc_pwl_stepwise.py tests/test_nyr_ndcpwlf.py \
    tests/test_lera_stepwise_soundness.py tests/test_lera_memory_guard.py \
    tests/test_prover_fuzz_soundness.py > "$root/logs/correctness.log" 2>&1
touch "$root/logs/correctness.complete"
