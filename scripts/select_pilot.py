#!/usr/bin/env python3
"""Select historical strata without consulting new run outcomes (stdlib only)."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'references/dabia2013-n100-v011-2026-11-09'
PIN = 'ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66'


def select():
    raw = (SNAPSHOT / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN:
        raise ValueError('Reference manifest differs from frozen pin')
    rows = []
    for item in json.loads(raw)['instances']:
        path = SNAPSHOT / 'source-solutions' / Path(item['source_reference_path']).name
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != item['source_reference_sha256']:
            raise ValueError(f'Source-reference hash mismatch: {path}')
        record = json.loads(source)
        historical_proven = record.get('metadata', {}).get('optimality', {}).get('proven') is True
        rows.append({'instance': item['instance_name'], 'class': item['instance_name'][:-2],
                     'historical_stratum': 'declared_proven' if historical_proven else 'not_declared_proven',
                     'instance_sha256': item['instance_sha256'], 'source_reference_sha256': item['source_reference_sha256']})
    selected = []
    counts = {}
    for family in ('C1', 'C2', 'R1', 'R2', 'RC1', 'RC2'):
        pool = sorted((r for r in rows if r['class'] == family), key=lambda r: r['instance'])
        counts[family] = {s: sum(r['historical_stratum'] == s for r in pool)
                          for s in ('declared_proven', 'not_declared_proven')}
        chosen = []
        if family == 'RC1':
            chosen.append(next(r for r in pool if r['instance'] == 'RC105'))
        for stratum in ('declared_proven', 'not_declared_proven'):
            if len(chosen) < 2 and not any(r['historical_stratum'] == stratum for r in chosen):
                available = [r for r in pool if r['historical_stratum'] == stratum and r not in chosen]
                if available:
                    chosen.append(available[0])
        chosen.extend(r for r in pool if r not in chosen and len(chosen) < 2)
        selected.extend(chosen)
    return {'schema_version': 1, 'status': 'prespecified_selection_no_runtime_observations',
            'reference_manifest_sha256': PIN, 'historical_label_field': 'metadata.optimality.proven is true',
            'rule': 'Per class: reserve RC105 in RC1, fill missing declared-proven then not-declared-proven strata lexicographically, then lexicographic fallback to 2.',
            'warning': 'Historical labels are stratifiers only. No current proof credit or measured difficulty is inferred.',
            'stratum_counts': counts, 'selected_instances': selected}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = json.dumps(select(), indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    else:
        print(output, end='')
