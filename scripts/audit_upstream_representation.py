"""Compare native IGP data to materialized ATFs at all knots and midpoints."""
import json
from pathlib import Path
from checker_contract import checker_identity, sha256

checker_identity()
from mamut_routing_lib.td import load_td_instance
root = Path.home() / 'ms56-ablation'
results = []
for size in (25, 50):
    native_path = root / f'solver/upstream/instances/dabia_et_al_2013/C101_{size}.json'
    fixture_path = root / f'inputs/benchmarks/TDVRPTW/Dabia2013/n={size}/C101.vrp.json'
    native = json.loads(native_path.read_bytes())
    fixture = json.loads(fixture_path.read_bytes())
    loaded = load_td_instance(fixture_path, verify_sha256=True, validate_complete=True)
    metadata_matches = {
        'demands': native['demands'][:-1] == fixture['demands'],
        'service_times': native['service_times'][:-1] == fixture['service_times'],
        'time_windows': native['time_windows'][:-1] == fixture['time_windows'],
        'capacity': native['capacity'] == fixture['vehicle_capacity'],
        'vehicle_count': native['vehicle_count'] == fixture['num_vehicles'],
        'horizon': native['horizon'] == fixture['horizon'],
        'coordinates': native['digraph']['coordinates'][:-1] == fixture['coordinates'],
    }
    maximum = 0.0
    comparisons = 0
    outside_native_domain = 0
    outside_with_arrival_in_horizon = 0
    for (i, j), f in loaded.atfs.arcs.items():
        j2 = size + 1 if j == 0 else j
        distance = native['distances'][i][j2]
        speeds = native['cluster_speeds'][native['clusters'][i][j2]]
        def native_arrival(departure):
            remaining = distance
            now = departure
            for (left, right), speed in zip(native['speed_zones'], speeds):
                if right < departure:
                    continue
                available = right - max(left, departure)
                used = min(available, remaining / speed)
                now += used
                remaining -= used * speed
                if abs(remaining) < 1e-6:
                    return now
            return None
        xs, ys = list(f.xs), list(f.ys)
        for k, (x, y) in enumerate(zip(xs, ys)):
            samples = [(x, y)]
            if k + 1 < len(xs) and xs[k + 1] > x:
                samples.append(((x + xs[k + 1]) / 2, (y + ys[k + 1]) / 2))
            for x, y in samples:
                actual = native_arrival(x)
                if actual is None:
                    outside_native_domain += 1
                    if y <= native["horizon"][1]:
                        outside_with_arrival_in_horizon += 1
                    continue
                maximum = max(maximum, abs(actual - y))
                comparisons += 1
    results.append({'customers': size, 'native_sha256': sha256(native_path),
                    'fixture_sha256': sha256(fixture_path), 'metadata_matches': metadata_matches,
                    'atf_samples': comparisons, 'maximum_arrival_difference': maximum,
                    'outside_native_domain': outside_native_domain,
                    'outside_native_domain_with_arrival_within_horizon': outside_with_arrival_in_horizon,
                    'interpretation': 'Native speed horizon truncates late arrivals; MAMUT extends final speed. Feasible TDVRPTW arrivals must still satisfy depot horizon. Samples are numerical evidence, not a proof of exact floating-point identity.'})
output = root / 'solver/upstream-representation.json'
output.write_text(json.dumps(results, indent=2) + '\n')
