"""Audit all frozen n100 inputs against original native IGP representations."""
import argparse
import hashlib
import json
from pathlib import Path
from checker_contract import checker_identity, sha256


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--reference-manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    identity = checker_identity()
    from mamut_routing_lib.td import load_td_instance
    manifest_raw=args.reference_manifest.read_bytes()
    pin='ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66'
    if hashlib.sha256(manifest_raw).hexdigest()!=pin:
        raise ValueError('Unpinned reference manifest')
    results=[]
    for item in json.loads(manifest_raw)['instances']:
        name=item['instance_name']
        native_path=args.source_root/'instances/dabia_et_al_2013'/f'{name}_100.json'
        fixture_path=args.fixtures/f'{name}.vrp.json'
        if sha256(fixture_path)!=item['instance_sha256']:
            raise ValueError(f'Fixture mismatch {name}')
        native=json.loads(native_path.read_bytes());fixture=json.loads(fixture_path.read_bytes())
        atf_path=fixture_path.parent/fixture['td']['atf_path']
        if sha256(atf_path)!=item['atf_file_sha256']:
            raise ValueError(f'ATF mismatch {name}')
        loaded=load_td_instance(fixture_path,verify_sha256=True,validate_complete=True)
        matches={key:native[key][:-1]==fixture[key] for key in ('demands','service_times','time_windows')}
        matches.update(coordinates=native['digraph']['coordinates'][:-1]==fixture['coordinates'],
                       capacity=native['capacity']==fixture['vehicle_capacity'],
                       horizon=native['horizon']==fixture['horizon'],
                       vehicle_count=native['vehicle_count']==fixture['num_vehicles'],
                       native_depots=native['start_depot']==0 and native['end_depot']==101,
                       end_depot_duplicate=native['demands'][-1]==0 and native['service_times'][-1]==0 and native['time_windows'][-1]==native['time_windows'][0] and native['digraph']['coordinates'][-1]==native['digraph']['coordinates'][0],
                       native_arc_count=native['digraph']['arc_count']==sum(map(sum,native['digraph']['arcs']))==10100)
        worst=0.;samples=0;outside=0;outside_feasible=0;mismatches=0;arc_missing=0
        for (i,j),f in loaded.atfs.arcs.items():
            j2=101 if j==0 else j
            if not native['digraph']['arcs'][i][j2]:
                arc_missing+=1
            distance=native['distances'][i][j2]
            speeds=native['cluster_speeds'][native['clusters'][i][j2]]
            zones=native['speed_zones']
            def arrival(t):
                remaining=distance;now=t
                for (left,right),speed in zip(zones,speeds):
                    if right<t:continue
                    used=min(right-max(left,t),remaining/speed)
                    now+=used;remaining-=used*speed
                    if abs(remaining)<1e-6:return now
                return None
            # Include native IGP breakpoints as well as materialized knots.
            knots=set(float(x) for x in f.xs)
            for left,right in zones:
                knots.add(float(left));knots.add(float(right))
                remaining=distance;now=left
                for (l,r),speed in reversed(list(zip(zones,speeds))):
                    if l>left:continue
                    used=min(min(r,left)-l,remaining/speed)
                    now-=used;remaining-=used*speed
                    if abs(remaining)<1e-6:
                        if now>=native['horizon'][0]:knots.add(now)
                        break
            knots=sorted(x for x in knots if f.xs[0]<=x<=f.xs[-1])
            xs=list(f.xs);ys=list(f.ys)
            import bisect
            def materialized(t):
                k=min(max(0,bisect.bisect_right(xs,t)-1),len(xs)-2)
                return ys[k]+(t-xs[k])*(ys[k+1]-ys[k])/(xs[k+1]-xs[k])
            points=knots+[(a+b)/2 for a,b in zip(knots,knots[1:])]
            for x in points:
                y=materialized(x);actual=arrival(x)
                if actual is None:
                    outside+=1
                    if y<=native['horizon'][1]:outside_feasible+=1
                else:
                    delta=abs(actual-y);worst=max(worst,delta);samples+=1
                    if delta>1e-8:mismatches+=1
        results.append({'instance':name,'native_input_sha256':sha256(native_path),
                        'fixture_sha256':sha256(fixture_path),'atf_file_sha256':sha256(atf_path),
                        'metadata_matches':matches,'missing_native_arcs':arc_missing,
                        'samples':samples,'maximum_arrival_difference':worst,
                        'samples_over_1e_8':mismatches,'outside_native_domain':outside,
                        'outside_native_domain_with_arrival_within_horizon':outside_feasible})
    report={'checker':identity,'reference_manifest_sha256':pin,'instances':results,
            'status':'passed' if all(all(r['metadata_matches'].values()) and not any(r[k] for k in ('missing_native_arcs','samples_over_1e_8','outside_native_domain_with_arrival_within_horizon')) for r in results) else 'mismatch',
            'scope':'Numerical piecewise representation audit at union of native and materialized knots and interval midpoints. Native end depot maps to0. Late native-domain exclusions cannot arrive within the shared horizon. Does not establish exact floating-point route summation identity or pricing completeness.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
