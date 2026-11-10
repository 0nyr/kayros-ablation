"""Independent elementary-path feasibility and binary64-dual reduced-cost audit.

No master, pricing replay, full-solution route requirement, or completeness credit.
"""
from __future__ import annotations
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path

from checker_contract import checker_identity
from ms58_f2_capture_validate import (CaptureError,bits,fields,integer,need,path_ids,read_json,validate_capture)
from probe_runner import digest

ORIGINAL_SCHEMA='ms58-f2-original-route-evaluation/v1'
ORIGINAL_METHOD='networks2019::VRPInstance::BestDurationRoute'
DEFAULT_COST_TOLERANCE=1e-6
DEFAULT_ROBUST_MARGIN=1e-4


def original_evaluation(path,expected_sha256,capture,route):
    """Accept only separately pinned original-f2 evaluator evidence, never a fallback."""
    envelope,artifact_sha=read_json(path,expected_sha256,regular=True)
    fields(envelope,('record','complete'),'original evaluation envelope')
    need(list(envelope)==['record','complete'] and envelope['complete'] is True,'Incomplete or wrongly ordered original-f2 evaluation')
    record=envelope['record']
    fields(record,('schema','method','capture_sha256','native_input_sha256','source_inventory_sha256','path','duration_bits'),'original evaluation')
    identity=capture['expected_identity']
    need(record['schema']==ORIGINAL_SCHEMA and record['method']==ORIGINAL_METHOD,'Original evaluator method/schema mismatch')
    need(record['capture_sha256']==capture['capture_sha256'] and record['path']==route,'Original evaluation capture/path mismatch')
    need(record['native_input_sha256']==identity['UPSTREAM_NATIVE_INPUT_SHA256'] and
         record['source_inventory_sha256']==identity['MS58_DIAGNOSTIC_SOURCE_INVENTORY_SHA256'],'Original evaluation source/input mismatch')
    duration=bits(record['duration_bits'],'original duration_bits');need(duration>=0,'Negative original duration')
    return {'duration':duration,'artifact_sha256':artifact_sha,'method':record['method']}


def reduced_cost(cost,customers,dual_values,ordered_cuts,cut_values):
    """Customer-only coefficients, cut coefficient 1 iff at least 2 cut customers."""
    need(type(cost) in (int,float) and math.isfinite(cost),'Finite path cost required')
    visits=set(customers)
    coefficients=[int(len(visits.intersection(cut))>=2) for cut in ordered_cuts]
    profits=[dual_values[c] for c in customers]
    terms=[dual*coefficient for dual,coefficient in zip(cut_values,coefficients,strict=True)]
    # Preserve route-order and cut-order binary64 folds as a separate observation.
    customer_total=0.
    for value in profits:customer_total+=value
    cut_total=0.
    for value in terms:cut_total+=value
    folded=cost-customer_total-cut_total
    exact=Fraction.from_float(float(cost))
    for value in profits:exact-=Fraction.from_float(value)
    for value,coefficient in zip(cut_values,coefficients,strict=True):
        exact-=Fraction.from_float(value)*coefficient
    return {'binary64_fold':folded,'exact_binary64_inputs_numerator':str(exact.numerator),
        'exact_binary64_inputs_denominator':str(exact.denominator),'customer_dual_sum':customer_total,
        'cut_dual_sum':cut_total,'cut_coefficients':coefficients},exact


def check_route(loaded,path,vertex_count,forbidden_arcs,*,route_evaluator=None):
    """Validate one partial pricing route, never a full customer-cover solution."""
    customers=path_ids(path,vertex_count)
    need(loaded.instance.num_customers==vertex_count-2,'Checker instance dimension mismatch')
    forbidden=set()
    for arc in forbidden_arcs:
        need(isinstance(arc,(list,tuple)) and len(arc)==2,'Forbidden arc shape mismatch')
        for v in arc:integer(v,'forbidden vertex',0,vertex_count-1)
        forbidden.add(tuple(arc))
    need(not any((u,v) in forbidden for u,v in zip(path,path[1:])),'Pricing path uses a forbidden arc')
    # All original IDs were validated before the first demand-array access.
    demand=0
    for c in customers:
        value=loaded.instance.demands[c]
        need(type(value) in (int,float) and math.isfinite(value) and value>=0,'Invalid customer demand')
        demand+=value
    need(demand<=loaded.instance.vehicle_capacity,'Pricing route exceeds vehicle capacity')
    if route_evaluator is None:
        from mamut_routing_lib.td import compute_route_duration
        route_evaluator=compute_route_duration
    evaluation=route_evaluator(loaded.instance,loaded.atfs,customers)
    need(evaluation.feasible,'Pricing route violates time windows or timing feasibility')
    need(type(evaluation.duration) in (int,float) and math.isfinite(evaluation.duration) and evaluation.duration>=0,'Invalid checker duration')
    need(type(evaluation.departure_time) in (int,float) and math.isfinite(evaluation.departure_time),'Invalid checker departure')
    return {'customers':customers,'load':demand,'duration':evaluation.duration,'departure_time':evaluation.departure_time}


def inspect_path(capture,loaded,path,*,reported_native_duration_bits=None,original_evaluation_path=None,
                 original_evaluation_sha256=None,cost_tolerance=DEFAULT_COST_TOLERANCE,
                 robust_margin=DEFAULT_ROBUST_MARGIN,route_evaluator=None):
    checker=checker_identity()
    need(capture.get('terminal_eligible') is True and capture.get('structurally_replayable') is True,'A validated structurally replayable terminal capture is required')
    need(math.isfinite(cost_tolerance) and cost_tolerance>=0 and math.isfinite(robust_margin) and robust_margin>cost_tolerance,'Invalid numerical margins')
    record=capture['record'];master=record['raw_master'];query=record['native_query'];n=master['vertex_row_count']
    raw=capture['raw_input']
    need(loaded.instance.instance_name==raw['instance_name'] and loaded.instance.demands==raw['demands'] and
         loaded.instance.vehicle_capacity==raw['vehicle_capacity'],'Loaded checker instance differs from bound raw input')
    need(route_evaluator is None,'Counterexample inspection requires the pinned per-route evaluator')
    checked=check_route(loaded,path,n,query['forbidden_arcs'])
    # Require captured native graph arcs in addition to independent checker feasibility.
    native_arcs=set(map(tuple,record['labeling_instances']['bidirectional']['graph']['ordered_arcs']))
    need(all((u,v) in native_arcs for u,v in zip(path,path[1:])),'Path uses an arc absent from captured native graph')
    profits=[bits(v) for v in master['dual_bits'][:n]]
    raw_cuts=master['ordered_cuts'];raw_sigma=[bits(v) for v in master['dual_bits'][n:]]
    filtered_cuts=query['filtered_cuts'];filtered_sigma=[bits(v) for v in query['filtered_sigma_bits']]
    raw,raw_exact=reduced_cost(checked['duration'],checked['customers'],profits,raw_cuts,raw_sigma)
    filtered,filtered_exact=reduced_cost(checked['duration'],checked['customers'],profits,filtered_cuts,filtered_sigma)
    cost_disagreement=False;native=None
    if reported_native_duration_bits is not None:
        native=bits(reported_native_duration_bits,'reported_native_duration_bits')
        need(native>=0,'Negative reported native duration')
        cost_disagreement=abs(native-checked['duration'])>cost_tolerance
    need((original_evaluation_path is None)==(original_evaluation_sha256 is None),'Original evaluation path and trusted SHA-256 must be supplied together')
    original=None;original_raw=None;original_filtered=None
    if original_evaluation_path is not None:
        original=original_evaluation(original_evaluation_path,original_evaluation_sha256,capture,path)
        original_raw,original_raw_exact=reduced_cost(original['duration'],checked['customers'],profits,raw_cuts,raw_sigma)
        original_filtered,original_filtered_exact=reduced_cost(original['duration'],checked['customers'],profits,filtered_cuts,filtered_sigma)
        cost_disagreement|=abs(original['duration']-checked['duration'])>cost_tolerance
    threshold=Fraction.from_float(robust_margin)
    raw_negative=raw_exact < -threshold;filtered_negative=filtered_exact < -threshold
    stdin_verified=capture.get('stdin_binding',{}).get('status')=='verified'
    robust_raw=bool(stdin_verified and original is not None and not cost_disagreement and raw_negative and original_raw_exact < -threshold)
    robust_filtered=bool(stdin_verified and original is not None and not cost_disagreement and filtered_negative and original_filtered_exact < -threshold)
    if cost_disagreement:classification='native_vs_checker_cost_disagreement_requires_review'
    elif not stdin_verified:classification='stdin_instance_unbound_uncertified'
    elif original is None:classification='original_f2_cost_unavailable_uncertified'
    elif robust_raw:classification='robust_raw_master_counterexample_candidate'
    elif abs(raw_exact)<=threshold or abs(original_raw_exact)<=threshold:classification='near_zero_numerical_evidence'
    else:classification='no_robust_negative_route_observed'
    return {'checker':checker,'path':path,'path_sha256':digest(path),'checker_route':checked,'reported_native_duration':native,
        'original_f2_evaluation':original,'native_cost_disagreement':cost_disagreement,
        'raw_master_reduced_cost_checker':raw,'filtered_query_reduced_cost_checker':filtered,
        'raw_master_reduced_cost_original_f2':original_raw,'filtered_query_reduced_cost_original_f2':original_filtered,
        'robust_raw_master_counterexample_candidate':robust_raw,'robust_filtered_query_counterexample_candidate':robust_filtered,
        'classification':classification,'cost_tolerance':cost_tolerance,'robust_negative_margin':robust_margin,
        'depot_duals_ignored':[profits[0],profits[-1]],'capture_sha256':capture['capture_sha256'],
        'stdin_binding':capture.get('stdin_binding'),'derivative_event_proof_credit':False,'derivative_event_closure_credit':False,
        'scope':'Independently feasible elementary partial route. No counterexample found, no returned route, or an empty replay pool is not a completeness proof. A pricing counterexample does not by itself disprove an integer optimum.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('capture','manifest','native-input','raw-input','source-inventory'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('capture-sha256','manifest-sha256','native-input-sha256','raw-input-sha256','source-inventory-sha256','expected-source-id','expected-run-id'):p.add_argument('--'+name,required=True)
    p.add_argument('--path',nargs='+',type=int,required=True);p.add_argument('--reported-native-duration-bits',type=int)
    p.add_argument('--original-evaluation',type=Path);p.add_argument('--original-evaluation-sha256')
    p.add_argument('--stdin-artifact',type=Path);p.add_argument('--stdin-sha256')
    p.add_argument('--native-segment-offset',type=int);p.add_argument('--native-segment-length',type=int);p.add_argument('--native-segment-sha256')
    a=p.parse_args();capture=validate_capture(a.capture,a.manifest,a.native_input,a.raw_input,a.source_inventory,
        capture_sha256=a.capture_sha256,manifest_sha256=a.manifest_sha256,native_input_sha256=a.native_input_sha256,
        raw_input_sha256=a.raw_input_sha256,source_inventory_sha256=a.source_inventory_sha256,
        expected_source_id=a.expected_source_id,expected_run_id=a.expected_run_id,stdin_artifact=a.stdin_artifact,
        stdin_sha256=a.stdin_sha256,native_segment_offset=a.native_segment_offset,native_segment_length=a.native_segment_length,
        native_segment_sha256=a.native_segment_sha256)
    identity=checker_identity()
    from mamut_routing_lib.td import load_td_instance
    read_json(a.raw_input,capture['expected_identity']['MS58_DIAGNOSTIC_RAW_INPUT_SHA256'])
    loaded=load_td_instance(a.raw_input,verify_sha256=True,validate_complete=True)
    read_json(a.raw_input,capture['expected_identity']['MS58_DIAGNOSTIC_RAW_INPUT_SHA256'])
    result=inspect_path(capture,loaded,a.path,reported_native_duration_bits=a.reported_native_duration_bits,
        original_evaluation_path=a.original_evaluation,original_evaluation_sha256=a.original_evaluation_sha256)
    result['checker']=identity
    print(json.dumps(result,indent=2,allow_nan=False))
    return int(result['native_cost_disagreement'])


if __name__=='__main__':raise SystemExit(main())
