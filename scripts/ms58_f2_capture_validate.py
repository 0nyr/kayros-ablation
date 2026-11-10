"""Validate the concrete ms58 f2 capture contract without reconstruction or replay."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct

from checker_contract import CHECKER_COMMIT,CHECKER_TREE
from probe_runner import digest

SCHEMA='ms58-f2-terminal-pricing/v1'
BASE='f2c9e205bd1ecc9afb45d5efe7fcb0df881f2b33'
ENCODING='f2-legacy-scalar-binary64-uint64'
EPS=1e-5
INFTY=1e51
IDENTITY_FIELDS=('PROBE_MANIFEST_HASH','PROBE_RUN_ID','UPSTREAM_NATIVE_INPUT_SHA256',
                 'MS58_DIAGNOSTIC_RAW_INPUT_SHA256','MS58_DIAGNOSTIC_SOURCE_INVENTORY_SHA256')
EXPECTED_CHECKER={'package':'mamut-routing-lib','version':'0.11.0','commit':CHECKER_COMMIT,
    'python_files':23,'installed_python_tree_sha256':CHECKER_TREE}


class CaptureError(ValueError):pass


def need(condition,message):
    if not condition:raise CaptureError(message)


def integer(value,name,lo=0,hi=(1<<64)-1):
    need(type(value) is int and lo<=value<=hi,f'{name}: integer outside permitted range')
    return value


def bits(value,name='binary64'):
    integer(value,name)
    result=struct.unpack('<d',struct.pack('<Q',value))[0]
    need(math.isfinite(result),f'{name}: nonfinite binary64')
    return result


def encode(value):return struct.unpack('<Q',struct.pack('<d',float(value)))[0]


def shape(value,n,name):
    need(isinstance(value,list) and len(value)==n,f'{name}: expected list length {n}')
    return value


def fields(value,required,name,optional=()):
    need(isinstance(value,dict),f'{name}: expected object')
    need(set(required)<=value.keys(),f'{name}: missing fields {sorted(set(required)-value.keys())}')
    need(value.keys()<=set(required)|set(optional),f'{name}: unknown fields {sorted(value.keys()-set(required)-set(optional))}')


def pair(value,name):return [bits(v,f'{name}[{i}]') for i,v in enumerate(shape(value,2,name))]


def no_duplicate_object(pairs):
    out={}
    for key,value in pairs:
        need(key not in out,'Duplicate JSON object key: '+key);out[key]=value
    return out


def read_json(path,expected_sha256=None,regular=False):
    path=Path(path)
    if regular:
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            need(stat.S_ISREG(os.fstat(descriptor).st_mode),'Capture must be a regular file')
            with os.fdopen(descriptor,'rb',closefd=False) as stream:data=stream.read()
        finally:os.close(descriptor)
    else:data=path.read_bytes()
    actual=hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None:need(actual==expected_sha256,f'{path}: file SHA-256 mismatch')
    try:document=json.loads(data,object_pairs_hook=no_duplicate_object,
        parse_constant=lambda value:(_ for _ in ()).throw(CaptureError('Nonstandard JSON number: '+value)))
    except (json.JSONDecodeError,UnicodeDecodeError) as error:raise CaptureError('Incomplete or invalid JSON capture') from error
    return document,actual


def stdin_binding(path,expected_sha256,offset,length,segment_sha256,native_path,native_sha256,experiment):
    """Verify the actual frozen stdin segment, not a producer's pathname claim."""
    integer(offset,'native stdin segment offset');integer(length,'native stdin segment length',1)
    descriptor=os.open(Path(path),os.O_RDONLY|os.O_NOFOLLOW)
    try:
        need(stat.S_ISREG(os.fstat(descriptor).st_mode),'Stdin artifact must be a regular file')
        with os.fdopen(descriptor,'rb',closefd=False) as stream:data=stream.read()
    finally:os.close(descriptor)
    actual=hashlib.sha256(data).hexdigest();need(actual==expected_sha256,'Full stdin artifact SHA-256 mismatch')
    need(offset+length<=len(data),'Native stdin segment exceeds artifact')
    segment=data[offset:offset+length];native=Path(native_path).read_bytes()
    need(hashlib.sha256(native).hexdigest()==native_sha256,'Reloaded native file changed before stdin audit')
    need(hashlib.sha256(segment).hexdigest()==segment_sha256==native_sha256 and segment==native,
         'Stdin native segment is not byte-identical to pinned reloaded native file')
    try:
        before=json.loads(data[:offset],object_pairs_hook=no_duplicate_object)
        json.loads(data[offset+length:],object_pairs_hook=no_duplicate_object)
    except (json.JSONDecodeError,UnicodeDecodeError) as error:raise CaptureError('Stdin must frame experiment/native/solutions as exactly 3 JSON values') from error
    need(before==experiment,'Stdin experiment differs from captured actual experiment')
    need(hashlib.sha256(Path(native_path).read_bytes()).hexdigest()==native_sha256,'Reloaded native file changed after stdin audit')
    return {'status':'verified','artifact_sha256':actual,'native_segment_offset':offset,'native_segment_length':length,
            'native_segment_sha256':segment_sha256,'experiment_matches':True,
            'scope':'Exact frozen stdin bytes and segment verified. Launcher must separately attest that this file was actual subprocess stdin.'}


def function_record(function,name):
    fields(function,('encoding','cached_domain_bits','cached_image_bits','piece_count','pieces'),name)
    need(function['encoding']==ENCODING,name+': wrong function encoding')
    domain=pair(function['cached_domain_bits'],name+'.cached_domain_bits')
    image=pair(function['cached_image_bits'],name+'.cached_image_bits')
    count=integer(function['piece_count'],name+'.piece_count')
    pieces=shape(function['pieces'],count,name+'.pieces')
    issues=[]
    for index,p in enumerate(pieces):
        label=f'{name}.pieces[{index}]';fields(p,('domain_bits','image_bits','slope_bits','intercept_bits'),label)
        pd=pair(p['domain_bits'],label+'.domain_bits');pi=pair(p['image_bits'],label+'.image_bits')
        bits(p['slope_bits'],label+'.slope_bits');bits(p['intercept_bits'],label+'.intercept_bits')
        if pd[0]>pd[1]:issues.append(label+': inverted piece domain')
        if pi[0]>pi[1]:issues.append(label+': inverted piece image')
    if not pieces:
        if domain!=[INFTY,-INFTY] or image!=[INFTY,-INFTY]:issues.append(name+': empty cached sentinels differ from f2 defaults')
    else:
        if domain!=[bits(pieces[0]['domain_bits'][0]),bits(pieces[-1]['domain_bits'][1])]:issues.append(name+': cached domain differs from first/last pieces')
        if image!=[min(bits(p['image_bits'][0]) for p in pieces),max(bits(p['image_bits'][1]) for p in pieces)]:issues.append(name+': cached image differs from piece-image union')
        if domain[0]>domain[1]:issues.append(name+': inverted cached domain')
        # Preserve possible dust, holes, scalar point image spans, and source order.
        for previous,current in zip(pieces,pieces[1:]):
            if bits(current['domain_bits'][0])+EPS<bits(previous['domain_bits'][1]):issues.append(name+': overlapping/out-of-order domains beyond f2 EPS')
    return issues


def vertex(value,n,name,customer=False):
    return integer(value,name,1 if customer else 0,n-2 if customer else n-1)


def arcs(value,n,name):
    need(isinstance(value,list),name+': expected arc list');result=[]
    for i,a in enumerate(value):
        shape(a,2,f'{name}[{i}]');result.append(tuple(vertex(v,n,f'{name}[{i}]') for v in a))
    need(len(set(result))==len(result),name+': duplicate arc')
    return result


def cuts(value,count,n,name):
    shape(value,count,name)
    for index,cut in enumerate(value):
        shape(cut,3,f'{name}[{index}]')
        for v in cut:vertex(v,n,f'{name}[{index}]',customer=True)
        need(len(set(cut))==3,name+': cut customers must be distinct')
    return value


def instance_record(value,n,name,origin,destination):
    required=('vertex_count','origin','destination','horizon_bits','capacity_bits','windows_bits','demands_bits','graph','tau','arr','dep','pretau','ldt_bits')
    fields(value,required,name);need(value['vertex_count']==n,name+': vertex count mismatch')
    vertex(value['origin'],n,name+'.origin');vertex(value['destination'],n,name+'.destination')
    need((value['origin'],value['destination'])==(origin,destination),name+': depot direction mismatch')
    need(bits(value['horizon_bits'])>0 and bits(value['capacity_bits'])>0,name+': nonpositive horizon/capacity')
    for i,w in enumerate(shape(value['windows_bits'],n,name+'.windows_bits')):
        left,right=pair(w,f'{name}.windows_bits[{i}]');need(left<=right,name+': inverted time window')
    for v in shape(value['demands_bits'],n,name+'.demands_bits'):need(bits(v)>=0,name+': negative demand')
    graph=value['graph'];fields(graph,('successors','predecessors','ordered_arcs'),name+'.graph')
    edges=arcs(graph['ordered_arcs'],n,name+'.graph.ordered_arcs');successors=[];predecessors=[]
    for direction,target in [('successors',successors),('predecessors',predecessors)]:
        for v,neighbors in enumerate(shape(graph[direction],n,name+'.graph.'+direction)):
            need(isinstance(neighbors,list),name+': adjacency list required')
            for w in neighbors:vertex(w,n,name+'.graph.'+direction)
            need(len(set(neighbors))==len(neighbors),name+': duplicate neighbor')
            target.extend((v,w) if direction=='successors' else (w,v) for w in neighbors)
    need(set(edges)==set(successors)==set(predecessors),name+': graph arc/adjacency mismatch')
    issues=[]
    for table in ('tau','arr','dep','pretau'):
        for i,row in enumerate(shape(value[table],n,name+'.'+table)):
            for j,f in enumerate(shape(row,n,name+'.'+table)):
                issues.extend(function_record(f,f'{name}.{table}[{i}][{j}]'))
    for row in shape(value['ldt_bits'],n,name+'.ldt_bits'):
        for v in shape(row,n,name+'.ldt_bits'):bits(v,name+'.ldt_bits')
    return issues


def entry_policy(value,terminal):
    required=('call_ordinal','callback_generation','level','closing_state','merge_start','solution_limit','partial',
        'relax_elementary_check','relax_cost_check','limited_extension','lazy_extension','unreachable_strengthened','sort_by_cost',
        'correcting','symmetric','process_limits','cross','time_budget_seconds_bits','initial_middle_bits','pool_bucket_count',
        'pool_max_load_factor_bits','returned_status','finished','route_count','source_iteration_index',
        'time_budget_milliseconds_bits','callback_budget_milliseconds_bits')
    fields(value,required,'entry_policy')
    for name in ('call_ordinal','callback_generation','merge_start','solution_limit','pool_bucket_count','route_count','source_iteration_index'):
        integer(value[name],'entry_policy.'+name)
    integer(value['level'],'entry_policy.level',0,2);integer(value['returned_status'],'entry_policy.returned_status',0,3)
    for name in ('closing_state','partial','relax_elementary_check','relax_cost_check','limited_extension','lazy_extension',
                 'unreachable_strengthened','sort_by_cost','correcting','symmetric','finished'):
        need(type(value[name]) is bool,'entry_policy.'+name+': boolean required')
    for limit in shape(value['process_limits'],2,'entry_policy.process_limits'):integer(limit,'process_limit',0,(1<<31)-1)
    for cross in shape(value['cross'],2,'entry_policy.cross'):need(type(cross) is bool,'cross: boolean required')
    need(bits(value['time_budget_seconds_bits'])>=0,'Negative time budget')
    milliseconds=bits(value['time_budget_milliseconds_bits'])
    need(milliseconds>=0 and bits(value['callback_budget_milliseconds_bits'])>=0,'Negative millisecond budget')
    need(value['time_budget_seconds_bits']==encode(milliseconds/1000.0),'Labeler seconds/milliseconds disagree')
    for x in shape(value['initial_middle_bits'],2,'initial_middle_bits'):bits(x)
    load=bits(value['pool_max_load_factor_bits']);need((0<load if terminal else 0<=load) and load<=3.4028234663852886e38 and float(struct.unpack('<f',struct.pack('<f',load))[0])==load,'Pool max load factor must be exact widened float')
    if terminal:
        need(value['call_ordinal']>0 and value['callback_generation']>0,'Missing terminal call/generation')
        need(value['level']==2 and value['returned_status']==3 and value['finished'] is True and value['route_count']==0,'Terminal call must be Exact, Finished, empty')
        need(not value['correcting'] and not value['relax_elementary_check'] and not value['relax_cost_check'],'f2 Exact policy/correction mismatch')


def numeric_bits(value,number_key,bits_key,name):
    need(type(value[number_key]) in (int,float) and math.isfinite(value[number_key]),name+': finite JSON number required')
    need(encode(value[number_key])==value[bits_key],name+': JSON number and binary64 disagree')
    return bits(value[bits_key],name+'.'+bits_key)


def path_ids(path,n,name='path'):
    need(isinstance(path,list) and 3<=len(path)<=n,name+': complete depot-to-depot elementary path required')
    # Check every original ID before any input array access or depot conversion.
    for v in path:vertex(v,n,name)
    need(path[0]==0 and path[-1]==n-1,name+': invalid depots')
    for v in path[1:-1]:vertex(v,n,name,customer=True)
    need(len(set(path[1:-1]))==len(path)-2,name+': repeated customer')
    return path[1:-1]


def effective_settings(value,experiment=None):
    flags=('partial','limited_extension','lazy_extension','unreachable_strengthened','sort_by_cost','symmetric','iterative_merge','exact_labeling','correcting')
    fields(value,('cut_limit','node_limit',*flags,'requested_time_limit_seconds_bits','bcp_time_budget_seconds_bits','eps_bits'),'effective_settings')
    integer(value['cut_limit'],'effective_settings.cut_limit',0,500)
    integer(value['node_limit'],'effective_settings.node_limit',0,(1<<31)-1)
    for key in flags:need(type(value[key]) is bool,'effective_settings.'+key+': boolean required')
    need(value['correcting'] is False and value['eps_bits']==encode(EPS),'f2 correction/EPS setting mismatch')
    need(bits(value['requested_time_limit_seconds_bits'])>=0 and bits(value['bcp_time_budget_seconds_bits'])>=0,'Negative requested/BCP budget')
    if experiment is not None:
        need(isinstance(experiment,dict),'Actual experiment object required')
        defaults={'cut_limit':100,'node_limit':(1<<31)-1,'partial':True,'limited_extension':True,'lazy_extension':True,
            'unreachable_strengthened':True,'sort_by_cost':True,'symmetric':False,'iterative_merge':True,'exact_labeling':True}
        for key,default in defaults.items():need(value[key]==experiment.get(key,default),'Parsed effective experiment mismatch: '+key)
        requested=experiment.get('time_limit',7200.0)
        need(type(requested) in (int,float) and math.isfinite(requested) and requested>=0,'Invalid experiment time limit')
        # Duration stores milliseconds before reporting seconds.
        need(value['requested_time_limit_seconds_bits']==encode((float(requested)*1000.0)/1000.0),'Parsed requested duration mismatch')


def validate_record(record,expected_identity,native_input,raw_input,*,initial_c104_scope=True):
    need(isinstance(record,dict),'Record object required')
    need(record.get('schema')==SCHEMA,'Wrong capture schema')
    need(record.get('identity')==expected_identity,'Source/run/manifest/input identity mismatch')
    need(expected_identity.get('base_source')==BASE,'Wrong f2 base source')
    fields(expected_identity,('base_source',*IDENTITY_FIELDS,'MS58_DIAGNOSTIC_NATIVE_INPUT_PATH'),'expected_identity')
    need(isinstance(expected_identity['MS58_DIAGNOSTIC_NATIVE_INPUT_PATH'],str) and expected_identity['MS58_DIAGNOSTIC_NATIVE_INPUT_PATH'],'Native input path provenance required')
    for key in IDENTITY_FIELDS:need(isinstance(expected_identity[key],str) and re.fullmatch('[0-9a-f]{64}',expected_identity[key]),key+': SHA-256 required')
    kind=record.get('kind');common=('schema','kind','identity')
    if kind=='invalid_compose':
        extra=('site','outer_index','inner_piece_index','outer_piece_count','inner_piece_count','outer','inner','phase','call_ordinal','callback_generation')
        fields(record,(*common,*extra),'invalid_compose')
        need(record['site'] in ('constant_descending_search','decreasing_descending_search'),'Unknown Compose guard site')
        for k in ('outer_piece_count','inner_piece_count','call_ordinal','callback_generation'):integer(record[k],k)
        integer(record['outer_index'],'outer_index',-(1<<31),(1<<31)-1)
        integer(record['inner_piece_index'],'inner_piece_index',0,record['inner_piece_count']-1)
        integer(record['phase'],'phase',0,4)
        issues=function_record(record['outer'],'outer')+function_record(record['inner'],'inner')
        need(record['outer_piece_count']==record['outer']['piece_count'] and record['inner_piece_count']==record['inner']['piece_count'],'Operand piece count mismatch')
        need(not 0<=record['outer_index']<record['outer_piece_count'],'Reported Compose index is valid')
        return {'kind':kind,'terminal_eligible':False,'function_structure_issues':issues}
    if kind=='capture_failure':
        fields(record,(*common,'reason','phase','call_ordinal','callback_generation'),'capture_failure')
        need(isinstance(record['reason'],str) and record['reason'],'Capture failure reason required')
        integer(record['phase'],'phase',0,4)
        for key in ('call_ordinal','callback_generation'):integer(record[key],key)
        return {'kind':kind,'terminal_eligible':False,'reason':record['reason']}
    if kind=='no_terminal_capture':
        fields(record,(*common,'reason','entry_policy','proof_acceptance','source_bcp_log','effective_settings'),'no_terminal_capture')
        need(isinstance(record['reason'],str) and record['reason'],'Nonterminal reason required')
        need(record['proof_acceptance']=='diagnostic_only_no_closure_credit','Nonterminal proof acceptance mismatch')
        need(isinstance(record['source_bcp_log'],dict),'BCP log object required');entry_policy(record['entry_policy'],False)
        effective_settings(record['effective_settings'])
        return {'kind':kind,'terminal_eligible':False,'reason':record['reason']}
    need(kind=='terminal_root_pricing','Unknown capture kind')
    fields(record,(*common,'proof_acceptance','serialization_phase','source_returned_route_count','terminal_returned_paths',
        'entry_policy','raw_master','native_query','labeling_instances','source_root_cg_log','source_final_pricing_iteration',
        'returned_witness','original_instance','experiment','source_bcp_log','processed_node_count','effective_settings'),'terminal_root_pricing')
    need(record['proof_acceptance']=='diagnostic_only_no_closure_credit' and record['serialization_phase']=='after_bcp_run_return','Capture timing/credit contract mismatch')
    need(type(record['source_returned_route_count']) is int and record['source_returned_route_count']==0 and record['terminal_returned_paths']==[],'Terminal source returned paths must be empty')
    entry=record['entry_policy'];entry_policy(entry,True)
    bcp=record['source_bcp_log'];need(type(bcp.get('nodes_closed')) is int and type(bcp.get('nodes_open')) is int,'BCP node counts must be integers');need(isinstance(bcp,dict) and bcp.get('status')=='Optimum' and bcp.get('nodes_closed')==1 and bcp.get('nodes_open')==0,'Final BCP must be root-only Optimum')
    need(type(record['processed_node_count']) is int and record['processed_node_count']==1,'Processed node count mismatch')
    master=record['raw_master'];fields(master,('callback_generation','pricing_node_count','vertex_row_count','cut_count','row_count','dual_bits','ordered_cuts','dual_semantics','depot_route_coefficients'),'raw_master')
    n=integer(master['vertex_row_count'],'raw_master.vertex_row_count',3,102);k=integer(master['cut_count'],'raw_master.cut_count',0,500)
    need(master['pricing_node_count']==1 and type(master['pricing_node_count']) is int,'Raw pricing node count mismatch')
    need(master['callback_generation']==entry['callback_generation'],'Callback generation mismatch')
    need(master['row_count']==n+k and type(master['row_count']) is int,'LP row count must include both depot rows and all cuts')
    for v in shape(master['dual_bits'],n+k,'raw_master.dual_bits'):bits(v,'raw dual')
    cuts(master['ordered_cuts'],k,n,'raw_master.ordered_cuts')
    need(master['dual_semantics']=='duals_passed_to_terminal_pricing_not_postsolve_duals' and master['depot_route_coefficients']==[0,0] and all(type(v) is int for v in master['depot_route_coefficients']),'Raw dual semantics/depot coefficient mismatch')
    query=record['native_query'];fields(query,('profit_bits','forbidden_arcs','filtered_cuts','filtered_sigma_bits','raw_cut_indices','eps_bits','filter_predicate'),'native_query')
    for v in shape(query['profit_bits'],n,'native_query.profit_bits'):bits(v)
    need(query['profit_bits']==master['dual_bits'][:n],'Profit bits do not reconstruct raw vertex duals')
    need(query['eps_bits']==encode(EPS) and query['filter_predicate']=='fabs(dual)>=EPS','Wrong f2 cut filtering predicate/EPS')
    indices=[i for i,v in enumerate(master['dual_bits'][n:]) if abs(bits(v))>=EPS]
    need(query['raw_cut_indices']==indices and all(type(i) is int for i in query['raw_cut_indices']),'Filtered raw cut indices mismatch')
    need(query['filtered_cuts']==[master['ordered_cuts'][i] for i in indices],'Filtered cuts do not reconstruct raw order')
    need(query['filtered_sigma_bits']==[master['dual_bits'][n+i] for i in indices],'Filtered sigma bits mismatch')
    need(arcs(query['forbidden_arcs'],n,'native_query.forbidden_arcs')==[],'Initial terminal capture requires empty forbidden arcs')
    need(raw_input['num_customers']==n-2,'Materialized customer count mismatch')
    if initial_c104_scope:need(n==102 and raw_input['instance_name']=='C104' and native_input['instance_name']=='C104_100','Outside initial C104 n100 capture scope')
    need(record['original_instance']==native_input,'Original native input object differs from pinned original bytes')
    need(native_input['start_depot']==0 and native_input['end_depot']==n-1 and native_input['digraph']['vertex_count']==n,'Original native depots/dimension mismatch')
    for native_key,raw_key in [('demands','demands'),('service_times','service_times'),('time_windows','time_windows')]:
        need(native_input[native_key][:-1]==raw_input[raw_key],'Original native/materialized metadata mismatch: '+native_key)
    need(native_input['capacity']==raw_input['vehicle_capacity'] and native_input['vehicle_count']==raw_input['num_vehicles'] and native_input['horizon']==raw_input['horizon'],'Original capacity/fleet/horizon mismatch')
    instances=record['labeling_instances'];fields(instances,('bidirectional','forward','backward','returned_pool_bucket_count','returned_pool_max_load_factor_bits'),'labeling_instances')
    integer(instances['returned_pool_bucket_count'],'returned_pool_bucket_count');need(bits(instances['returned_pool_max_load_factor_bits'])>0,'Invalid returned pool load factor')
    issues=[]
    for name,o,d in [('bidirectional',0,n-1),('forward',0,n-1),('backward',n-1,0)]:issues.extend(instance_record(instances[name],n,'labeling_instances.'+name,o,d))
    forward=instances['forward'];bi=instances['bidirectional'];back=instances['backward']
    for name in ('tau','arr','dep','pretau','ldt_bits','windows_bits','demands_bits','horizon_bits','capacity_bits'):
        need(forward[name]==bi[name],'Forward and bidirectional stored instance differ: '+name)
    need(set(map(tuple,back['graph']['ordered_arcs']))=={(v,u) for u,v in forward['graph']['ordered_arcs']},'Reverse graph mismatch')
    need(back['demands_bits']==forward['demands_bits'] and back['capacity_bits']==forward['capacity_bits'] and back['horizon_bits']==forward['horizon_bits'],'Reverse scalar resources differ')
    horizon=bits(forward['horizon_bits'])
    for f,b in zip(forward['windows_bits'],back['windows_bits']):need(b==[encode(horizon-bits(f[1])),encode(horizon-bits(f[0]))],'Reverse time windows mismatch')
    root=record['source_root_cg_log'];need(isinstance(root,dict) and root.get('status')=='Optimum' and isinstance(root.get('iterations'),list),'Root CG log mismatch')
    need(bcp.get('root_log')==root,'Final BCP root log differs from source root CG log')
    index=entry['source_iteration_index'];need(index==len(root['iterations'])-1 and index>=0,'Capture must bind the last source iteration')
    last=root['iterations'][index];need(last==record['source_final_pricing_iteration'] and last.get('iteration_name')=='Exact' and last.get('status')=='Finished','Final pricing source record mismatch')
    for direction in ('forward','backward'):
        log=last.get(direction);need(isinstance(log,dict),direction+': source work required')
        for key in ('processed_count','enumerated_count','extended_count','dominated_count'):integer(log.get(key),direction+'.'+key)
        need(isinstance(log.get('count_by_length'),list),direction+': length census required')
        for count in log['count_by_length']:integer(count,direction+'.count_by_length')
        need(sum(log['count_by_length'])==log['processed_count'],direction+': processed count/length census mismatch')
    witness=record['returned_witness'];fields(witness,('kd_type','value','value_bits','routes'),'returned_witness')
    need(witness['kd_type']=='vrp_solution' and isinstance(witness['routes'],list),'Returned witness shape mismatch')
    numeric_bits(witness,'value','value_bits','returned_witness')
    for i,r in enumerate(witness['routes']):
        fields(r,('path','t0','duration','t0_bits','duration_bits'),f'returned_witness.routes[{i}]')
        path_ids(r['path'],n);numeric_bits(r,'t0','t0_bits','witness route');numeric_bits(r,'duration','duration_bits','witness route')
    settings=record['effective_settings'];effective_settings(settings,record['experiment'])
    for key in ('partial','limited_extension','lazy_extension','unreachable_strengthened','sort_by_cost','symmetric','correcting'):
        need(entry[key] is settings[key],'Entry policy differs from parsed effective setting: '+key)
    need(settings['exact_labeling'] is True,'Terminal capture requires exact labeling')
    return {'kind':kind,'terminal_eligible':True,'vertex_count':n,'raw_cut_count':k,'filtered_cut_count':len(indices),
        'function_structure_issues':issues,'structurally_replayable':not issues,'terminal_completeness_certified':False,
        'raw_master':master,'native_query':query}


def validate_capture(capture_path,manifest_path,native_input_path,raw_input_path,source_inventory_path,*,
        capture_sha256,manifest_sha256,native_input_sha256,raw_input_sha256,source_inventory_sha256,expected_source_id,expected_run_id,initial_c104_scope=True,stdin_artifact=None,stdin_sha256=None,
        native_segment_offset=None,native_segment_length=None,native_segment_sha256=None):
    manifest,_=read_json(manifest_path,manifest_sha256)
    need(manifest.get('checker')==EXPECTED_CHECKER and manifest.get('backend','').lower()=='cplex' and manifest.get('source_id')==expected_source_id,'Frozen manifest checker/backend/source mismatch')
    _,source_sha=read_json(source_inventory_path,source_inventory_sha256)
    native,native_sha=read_json(native_input_path,native_input_sha256);raw,raw_sha=read_json(raw_input_path,raw_input_sha256)
    expected={'base_source':BASE,'PROBE_MANIFEST_HASH':digest(manifest),'PROBE_RUN_ID':expected_run_id,
        'UPSTREAM_NATIVE_INPUT_SHA256':native_sha,'MS58_DIAGNOSTIC_RAW_INPUT_SHA256':raw_sha,
        'MS58_DIAGNOSTIC_SOURCE_INVENTORY_SHA256':source_sha,
        'MS58_DIAGNOSTIC_NATIVE_INPUT_PATH':manifest.get('diagnostic_environment',{}).get('MS58_DIAGNOSTIC_NATIVE_INPUT_PATH')}
    for section in ('diagnostic_environment','worker_environment'):
        for key,value in manifest.get(section,{}).items():
            if key in expected:need(value==expected[key],'Manifest environment binding mismatch: '+key)
    envelope,actual=read_json(capture_path,capture_sha256,regular=True)
    fields(envelope,('record','complete'),'envelope');need(list(envelope)==['record','complete'],'Complete marker must be the last envelope field');need(envelope['complete'] is True,'Incomplete capture envelope')
    result=validate_record(envelope['record'],expected,native,raw,initial_c104_scope=initial_c104_scope)
    supplied=(stdin_artifact,stdin_sha256,native_segment_offset,native_segment_length,native_segment_sha256)
    need(all(v is None for v in supplied) or all(v is not None for v in supplied),'All stdin artifact/segment bindings must be supplied together')
    binding={'status':'unbound','reason':'No independently pinned full stdin artifact/native segment supplied'}
    if stdin_artifact is not None:
        need('experiment' in envelope['record'],'Stdin experiment binding requires a terminal record')
        binding=stdin_binding(stdin_artifact,stdin_sha256,native_segment_offset,native_segment_length,native_segment_sha256,
            native_input_path,native_input_sha256,envelope['record']['experiment'])
    result.update(stdin_binding=binding,derivative_event_proof_credit=False,derivative_event_closure_credit=False)
    result.update(capture_sha256=actual,expected_identity=expected,record=envelope['record'],raw_input=raw,
        publication='Complete envelope verified. Historical exclusive creation is source-attested, not provable from file bytes alone.')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('capture','manifest','native-input','raw-input','source-inventory'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('capture-sha256','manifest-sha256','native-input-sha256','raw-input-sha256','source-inventory-sha256','expected-source-id','expected-run-id'):p.add_argument('--'+name,required=True)
    p.add_argument('--stdin-artifact',type=Path);p.add_argument('--stdin-sha256')
    p.add_argument('--native-segment-offset',type=int);p.add_argument('--native-segment-length',type=int);p.add_argument('--native-segment-sha256')
    a=p.parse_args();r=validate_capture(a.capture,a.manifest,a.native_input,a.raw_input,a.source_inventory,
        capture_sha256=a.capture_sha256,manifest_sha256=a.manifest_sha256,native_input_sha256=a.native_input_sha256,
        raw_input_sha256=a.raw_input_sha256,source_inventory_sha256=a.source_inventory_sha256,
        expected_source_id=a.expected_source_id,expected_run_id=a.expected_run_id,stdin_artifact=a.stdin_artifact,
        stdin_sha256=a.stdin_sha256,native_segment_offset=a.native_segment_offset,native_segment_length=a.native_segment_length,
        native_segment_sha256=a.native_segment_sha256)
    print(json.dumps({k:v for k,v in r.items() if k not in ('record','raw_input','raw_master','native_query')},indent=2))
    return int(bool(r.get('function_structure_issues')))


if __name__=='__main__':raise SystemExit(main())
