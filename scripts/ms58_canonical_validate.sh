#!/usr/bin/env bash
set -euo pipefail
test "${OAR_JOB_ID:?}" = 7013766
root="$HOME/ms58-bpca"
out="$root/resume-20261110/canonical-validation"
mkdir "$out"
cd "$root/sources/stable-branch"
c++ -std=c++23 -O3 -DNDEBUG -ffp-contract=off \
    -Icpp/lera/goc/include -Icpp/lera/main/include \
    tests/cpp/test_ordered_valuation.cpp \
    cpp/lera/goc/src/linear_programming/highs/matrix_formulation.cpp \
    build/cplex/cpp/lera/liblera.a -ldl -lm -o "$out/ordered-valuation"
"$out/ordered-valuation" > "$out/ordered-valuation.log"
cd "$root/sources/canonical-normalize"
c++ -std=c++23 -O3 -DNDEBUG -ffp-contract=off \
    -Icpp/lera/goc/include -Icpp/lera/main/include \
    tests/cpp/test_label_normalization.cpp build/cplex/cpp/lera/liblera.a \
    -ldl -lm -o "$out/normalization"
"$out/normalization" > "$out/normalization.log"
export MS58_CANONICAL_VALIDATION="$out"
"$HOME/ms56-ablation/experiment/.venv/bin/python" - <<'PY'
from pathlib import Path
import hashlib
import json
import os
out = Path(os.environ['MS58_CANONICAL_VALIDATION'])
record = {'ordered_valuation_property_passed': True,
          'normalization_bitwise_property_passed': True,
          'reference_source_id': '37e4801', 'candidate_source_id': '0bec4d4',
          'oar_job_id': os.environ['OAR_JOB_ID'],
          'log_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.log')}}
(out / 'validation.json').write_text(json.dumps(record, indent=2) + '\n')
PY
taskset -c 5 "$HOME/ms56-ablation/experiment/.venv/bin/python" \
    "$root/experiment/scripts/ms58_canonical_pairs.py" --validation-record "$out/validation.json"
