#!/usr/bin/env python3
"""Compare the proofs and incumbents of an ms61 final run with the optimality certificates stored in the benchmark store.

Usage: python3 analysis/ms61_certificate_check.py FINAL_SUMMARY.json [--store DIR] [--rel-tol 1e-9]

The store directory holds the Dabia2013 n=100 TDVRPTW best-known solutions (`<instance>.bks.Duration.json`), whose
`metadata.optimality` block records a certificate (`proven`, `proven_optimum`). Those certificates were produced before
the partial-dominance repair of 2026-11-10, so this check is the first comparison of each of them with a repaired prover.

For every instance of the run:
  - with a stored certificate P: every accepted proof of the run must equal P, and no checked incumbent of the run may be
    below P. A proof different from P or an incumbent below P is a CONTRADICTION (exit code 1). It must be reported to
    Onyr and not resolved here;
  - without a stored certificate: an accepted proof is listed as a new proof, with its value against the frozen
    reference of the run (a value below the reference is an improved best-known solution).
Standard library only. Read-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# The store files the frozen reference of the campaign was taken from (MAMUT-routing commit 8e3aa71, 25 certificates).
DEFAULT_STORE = Path(__file__).resolve().parents[1] / 'references/dabia2013-n100-v011-2026-11-09/source-solutions'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('summary', type=Path)
    parser.add_argument('--store', type=Path, default=DEFAULT_STORE)
    parser.add_argument('--rel-tol', type=float, default=1e-9)
    parser.add_argument('--json', type=Path, help='write the report as JSON as well')
    args = parser.parse_args()
    summary = json.loads(args.summary.read_bytes())
    tol = args.rel_tol
    cells = summary['cells']
    report = {'store': str(args.store), 'rel_tol': tol, 'instances': {}, 'contradictions': []}
    counts = {'stored': 0, 'stored_reproved': 0, 'stored_not_reproved': 0, 'new_proofs': 0, 'improved_reference': 0}
    for name in summary['design']['instances']:
        stored_file = args.store / f'{name}.bks.Duration.json'
        stored = json.loads(stored_file.read_bytes())
        optimality = (stored.get('metadata') or {}).get('optimality') or {}
        certificate = optimality.get('proven_optimum') if optimality.get('proven') else None
        mine = [c for c in cells if c['instance'] == name]
        proofs = sorted({(c['final_value'], c['arm']) for c in mine if c.get('state') == 'proved' and c.get('final_value') is not None})
        proof_values = sorted({v for v, _ in proofs})
        best = min((c['best_checked_cost'] for c in mine if c.get('best_checked_cost') is not None), default=None)
        reference = mine[0]['reference_cost']
        row = {'stored_cost': stored['cost'], 'stored_certificate': certificate, 'reference_cost': reference,
               'proof_values': proof_values, 'proved_arms': sorted({a for _, a in proofs}), 'best_checked_cost': best}
        if certificate is not None:
            counts['stored'] += 1
            scale = max(abs(certificate), 1.0)
            wrong = [v for v in proof_values if abs(v - certificate) > tol * scale]
            if wrong:
                report['contradictions'].append(f'{name}: accepted proof at {wrong} differs from the stored certificate {certificate}')
            if best is not None and best < certificate - tol * scale:
                report['contradictions'].append(f'{name}: checked incumbent {best} below the stored certificate {certificate}')
            counts['stored_reproved' if proof_values else 'stored_not_reproved'] += 1
        elif proof_values:
            counts['new_proofs'] += 1
        if best is not None and best < reference - tol * max(abs(reference), 1.0):
            counts['improved_reference'] += 1
            row['improves_reference_by'] = reference - best
        report['instances'][name] = row
    report['counts'] = counts
    print(f"instances {len(report['instances'])}: stored certificates {counts['stored']} "
          f"(re-proved in this run {counts['stored_reproved']}, not re-proved {counts['stored_not_reproved']}), "
          f"new proofs {counts['new_proofs']}, frozen reference improved on {counts['improved_reference']}")
    for name, row in report['instances'].items():
        if row['stored_certificate'] is None and row['proof_values']:
            print(f"  NEW PROOF {name}: {row['proof_values']} by {row['proved_arms']} (reference {row['reference_cost']})")
        if 'improves_reference_by' in row:
            print(f"  IMPROVED {name}: best checked {row['best_checked_cost']} below reference {row['reference_cost']} by {row['improves_reference_by']:.6f}")
    for line in report['contradictions']:
        print('CONTRADICTION', line)
    print(f"contradictions: {len(report['contradictions'])}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=1) + '\n')
    return 1 if report['contradictions'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
