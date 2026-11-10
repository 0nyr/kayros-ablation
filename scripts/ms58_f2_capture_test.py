"""Concrete schema and independent tiny-route fixtures. No solver replay or build."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from checker_contract import checker_identity
import ms58_f2_capture_validate as capture
import ms58_f2_path_check as paths
from probe_runner import digest


def write(path,data):
    with path.open('x') as stream:json.dump(data,stream,allow_nan=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function(left=0.,right=100.,slope=1.,intercept=0.,empty=False):
    domain=[capture.encode(left),capture.encode(right)]
    endpoints=[slope*left+intercept,slope*right+intercept]
    image=[capture.encode(min(endpoints)),capture.encode(max(endpoints))]
    piece={'domain_bits':domain,'image_bits':image,'slope_bits':capture.encode(slope),'intercept_bits':capture.encode(intercept)}
    return {'encoding':capture.ENCODING,'cached_domain_bits':[capture.encode(capture.INFTY),capture.encode(-capture.INFTY)] if empty else domain,
        'cached_image_bits':[capture.encode(capture.INFTY),capture.encode(-capture.INFTY)] if empty else image,
        'piece_count':0 if empty else 1,'pieces':[] if empty else [piece]}


def instance(n=5,reverse=False):
    edges=[[u,v] for u in range(n) for v in range(n) if u!=v and u!=n-1 and v!=0]
    if reverse:edges=[[v,u] for u,v in edges]
    graph={'ordered_arcs':edges,'successors':[[v for u,v in edges if u==i] for i in range(n)],
           'predecessors':[[u for u,v in edges if v==i] for i in range(n)]}
    tables={name:[[function(empty=True) for _ in range(n)] for _ in range(n)] for name in ('tau','arr','dep','pretau')}
    for u in range(n):
        for v in range(n):
            if u==v:
                tables['arr'][u][v]=tables['dep'][u][v]=function()
                tables['tau'][u][v]=tables['pretau'][u][v]=function(slope=0)
            elif [u,v] in edges:
                tables['arr'][u][v]=function(right=99,intercept=1)
                tables['dep'][u][v]=function(left=1,intercept=-1)
                tables['tau'][u][v]=function(right=99,slope=0,intercept=1)
                tables['pretau'][u][v]=function(left=1,slope=0,intercept=1)
    return {'vertex_count':n,'origin':n-1 if reverse else 0,'destination':0 if reverse else n-1,
        'horizon_bits':capture.encode(100),'capacity_bits':capture.encode(10),
        'windows_bits':[[capture.encode(0),capture.encode(100)] for _ in range(n)],
        'demands_bits':[capture.encode(v) for v in [0,1,1,1,0]],'graph':graph,
        **tables,'ldt_bits':[[capture.encode(100) for _ in range(n)] for _ in range(n)]}


def record(native,identity):
    policy={'call_ordinal':3,'callback_generation':2,'level':2,'closing_state':False,'merge_start':1,'solution_limit':3000,
        'partial':True,'relax_elementary_check':False,'relax_cost_check':False,'limited_extension':True,'lazy_extension':True,
        'unreachable_strengthened':True,'sort_by_cost':True,'correcting':False,'symmetric':False,'process_limits':[2147483647]*2,
        'cross':[False,True],'time_budget_seconds_bits':capture.encode(50),'time_budget_milliseconds_bits':capture.encode(50000),
        'callback_budget_milliseconds_bits':capture.encode(49000),'initial_middle_bits':[capture.encode(100)]*2,
        'pool_bucket_count':11,'pool_max_load_factor_bits':capture.encode(1),'returned_status':3,'finished':True,'route_count':0,'source_iteration_index':0}
    master={'callback_generation':2,'pricing_node_count':1,'vertex_row_count':5,'cut_count':1,'row_count':6,
        'dual_bits':[capture.encode(v) for v in [700,2,2,0,900,5e-6]],'ordered_cuts':[[1,2,3]],
        'dual_semantics':'duals_passed_to_terminal_pricing_not_postsolve_duals','depot_route_coefficients':[0,0]}
    query={'profit_bits':master['dual_bits'][:5],'forbidden_arcs':[],'filtered_cuts':[],
        'filtered_sigma_bits':[],'raw_cut_indices':[],'eps_bits':capture.encode(capture.EPS),'filter_predicate':'fabs(dual)>=EPS'}
    work={'status':'Finished','processed_count':1,'enumerated_count':2,'extended_count':1,'dominated_count':0,'count_by_length':[0,1]}
    last={'iteration_name':'Exact','status':'Finished','forward':copy.deepcopy(work),'backward':copy.deepcopy(work)}
    root={'status':'Optimum','iterations':[last]}
    experiment={'cut_limit':500,'time_limit':60}
    settings={'cut_limit':500,'node_limit':2147483647,'partial':True,'limited_extension':True,'lazy_extension':True,
        'unreachable_strengthened':True,'sort_by_cost':True,'symmetric':False,'iterative_merge':True,'exact_labeling':True,'correcting':False,
        'requested_time_limit_seconds_bits':capture.encode(60),'bcp_time_budget_seconds_bits':capture.encode(59),'eps_bits':capture.encode(capture.EPS)}
    return {'schema':capture.SCHEMA,'kind':'terminal_root_pricing','identity':identity,
        'proof_acceptance':'diagnostic_only_no_closure_credit','serialization_phase':'after_bcp_run_return','source_returned_route_count':0,
        'terminal_returned_paths':[],'processed_node_count':1,'entry_policy':policy,'effective_settings':settings,
        'raw_master':master,'native_query':query,'labeling_instances':{'bidirectional':instance(),'forward':instance(),'backward':instance(reverse=True),
            'returned_pool_bucket_count':11,'returned_pool_max_load_factor_bits':capture.encode(1)},
        'source_root_cg_log':root,'source_final_pricing_iteration':last,'source_bcp_log':{'status':'Optimum','nodes_closed':1,'nodes_open':0,'root_log':root},
        'returned_witness':{'kd_type':'vrp_solution','value':4.,'value_bits':capture.encode(4.),'routes':[
            {'path':[0,1,2,3,4],'t0':0.,'duration':4.,'t0_bits':capture.encode(0.),'duration_bits':capture.encode(4.)}]},
        'original_instance':native,'experiment':experiment}


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);cls.count=0
        cls.checker=checker_identity()
        from mamut_routing_lib.td import InstanceATFs,NDCPWLF,save_instance_atfs,compute_atf_sha256,load_td_instance
        atfs=InstanceATFs(instance_name='TOY3',benchmark_name='Dabia2013',horizon=(0.,100.),num_customers=3,
            arcs={(i,j):NDCPWLF([0.,100.],[1.,101.]) for i in range(4) for j in range(4) if i!=j},generator={'name':'independent-unit-fixture'})
        save_instance_atfs(atfs,cls.root/'toy.atf.json')
        raw={'instance_name':'TOY3','instance_origin':'Solomon1987','benchmark_name':'Dabia2013','num_customers':3,
            'num_vehicles':3,'vehicle_capacity':10,'coordinates':[[0,0],[1,0],[2,0],[3,0]],'demands':[0,1,1,1],
            'service_times':[0.,0.,0.,0.],'depot':0,'horizon':[0.,100.],'time_windows':[[0.,100.]]*4,
            'td':{'model':'atf-ndcpwlf','atf_path':'toy.atf.json','atf_sha256':compute_atf_sha256(atfs)},'metadata':{}}
        native={'instance_name':'TOY3_3','start_depot':0,'end_depot':4,'capacity':10,'vehicle_count':3,
            'demands':[0,1,1,1,0],'service_times':[0.]*5,'time_windows':[[0.,100.]]*5,'horizon':[0.,100.],
            'digraph':{'vertex_count':5}}
        cls.raw=raw;cls.native=native;cls.source='a'*40
        cls.manifest={'schema_version':1,'checker':cls.checker,'backend':'CPLEX','source_id':cls.source,'diagnostic_environment':{'MS58_DIAGNOSTIC_NATIVE_INPUT_PATH':'/native/original/TOY3.json'}}
        cls.msha=write(cls.root/'manifest.json',cls.manifest);cls.nsha=write(cls.root/'native.json',native)
        cls.rsha=write(cls.root/'toy.vrp.json',raw);cls.ssha=write(cls.root/'source.json',{'base_source':capture.BASE,'source_id':cls.source})
        cls.identity={'base_source':capture.BASE,'PROBE_MANIFEST_HASH':digest(cls.manifest),'PROBE_RUN_ID':'b'*64,
            'UPSTREAM_NATIVE_INPUT_SHA256':cls.nsha,'MS58_DIAGNOSTIC_RAW_INPUT_SHA256':cls.rsha,
            'MS58_DIAGNOSTIC_SOURCE_INVENTORY_SHA256':cls.ssha,'MS58_DIAGNOSTIC_NATIVE_INPUT_PATH':'/native/original/TOY3.json'}
        cls.record=record(native,cls.identity)
        cls.loaded=load_td_instance(cls.root/'toy.vrp.json',verify_sha256=True,validate_complete=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def validate(self,r=None,envelope=None,**kwargs):
        type(self).count+=1;path=self.root/f'capture-{self.count}.json'
        data=envelope if envelope is not None else {'record':r if r is not None else self.record,'complete':True}
        sha=write(path,data)
        options=dict(capture_sha256=sha,manifest_sha256=self.msha,native_input_sha256=self.nsha,raw_input_sha256=self.rsha,source_inventory_sha256=self.ssha,
            expected_source_id=self.source,expected_run_id='b'*64,initial_c104_scope=False)
        bind=kwargs.pop('bind_stdin',True)
        if bind and data['record'].get('kind')=='terminal_root_pricing':
            native=(self.root/'native.json').read_bytes();prefix=json.dumps(data['record']['experiment']).encode()+b'\n'
            stdin=prefix+native+b'\n{}\n';stdin_path=self.root/f'stdin-{self.count}.bin'
            with stdin_path.open('xb') as stream:stream.write(stdin)
            options.update(stdin_artifact=stdin_path,stdin_sha256=hashlib.sha256(stdin).hexdigest(),
                native_segment_offset=len(prefix),native_segment_length=len(native),native_segment_sha256=self.nsha)
        options.update(kwargs)
        return capture.validate_capture(path,self.root/'manifest.json',self.root/'native.json',self.root/'toy.vrp.json',self.root/'source.json',**options)

    def original(self,context,path,duration,**changes):
        type(self).count+=1;target=self.root/f'cost-{self.count}.json'
        rec={'schema':paths.ORIGINAL_SCHEMA,'method':paths.ORIGINAL_METHOD,'capture_sha256':context['capture_sha256'],
            'native_input_sha256':self.nsha,'source_inventory_sha256':self.ssha,'path':path,'duration_bits':capture.encode(duration)}
        rec.update(changes);return target,write(target,{'record':rec,'complete':True})

    def test_valid_envelope_and_tiny_cut_remains_raw(self):
        c=self.validate();self.assertTrue(c['terminal_eligible']);self.assertTrue(c['structurally_replayable'])
        self.assertEqual(c['raw_cut_count'],1);self.assertEqual(c['filtered_cut_count'],0)
        self.assertEqual(c['record']['raw_master']['dual_bits'][5],capture.encode(5e-6))

    def test_filtered_context_exact_threshold_and_order(self):
        for dual in [capture.EPS,-capture.EPS]:
            r=copy.deepcopy(self.record);r['raw_master']['dual_bits'][5]=capture.encode(dual)
            r['native_query'].update(filtered_cuts=[[1,2,3]],filtered_sigma_bits=[capture.encode(dual)],raw_cut_indices=[0])
            self.assertEqual(self.validate(r)['filtered_cut_count'],1)
        r=copy.deepcopy(self.record);r['native_query']['filtered_sigma_bits']=[capture.encode(5e-6)]
        with self.assertRaises(capture.CaptureError):self.validate(r)

    def test_nonzero_depot_duals_are_not_coefficients(self):
        context=self.validate();result=paths.inspect_path(context,self.loaded,[0,1,2,4],reported_native_duration_bits=capture.encode(3.))
        self.assertEqual(result['depot_duals_ignored'],[700.,900.])
        self.assertEqual(result['filtered_query_reduced_cost_checker']['binary64_fold'],-1.)
        self.assertEqual(result['raw_master_reduced_cost_checker']['cut_coefficients'],[1])
        self.assertEqual(result['classification'],'original_f2_cost_unavailable_uncertified')

    def test_cut_coefficient_zero_one_two_three_visits(self):
        for customers,coefficient in [([],0),([1],0),([1,2],1),([1,2,3],1)]:
            result,_=paths.reduced_cost(10.,customers,[700.,2.,2.,0.,900.],[[1,2,3]],[5e-6])
            self.assertEqual(result['cut_coefficients'],[coefficient])

    def test_wrong_source_input_hash_and_generation(self):
        for key in ['base_source',*capture.IDENTITY_FIELDS]:
            r=copy.deepcopy(self.record);r['identity'][key]='0'*len(r['identity'][key])
            with self.subTest(key=key),self.assertRaises(capture.CaptureError):self.validate(r)
        with self.assertRaises(capture.CaptureError):self.validate(expected_source_id='f'*40)
        with self.assertRaises(capture.CaptureError):self.validate(capture_sha256='f'*64)
        r=copy.deepcopy(self.record);r['raw_master']['callback_generation']=999
        with self.assertRaises(capture.CaptureError):self.validate(r)

    def test_trusted_input_pins_and_native_path_provenance(self):
        with self.assertRaises(capture.CaptureError):self.validate(native_input_sha256='f'*64)
        with self.assertRaises(capture.CaptureError):self.validate(raw_input_sha256='e'*64)
        r=copy.deepcopy(self.record);r['identity']['MS58_DIAGNOSTIC_NATIVE_INPUT_PATH']='/different/original.json'
        with self.assertRaises(capture.CaptureError):self.validate(r)
        # The checked local native file is relocated from the recorded source pathname.
        c=self.validate();self.assertEqual(c['expected_identity']['MS58_DIAGNOSTIC_NATIVE_INPUT_PATH'],'/native/original/TOY3.json')

    def test_all_ordered_cut_rows_and_filtered_indices(self):
        r=copy.deepcopy(self.record)
        r['raw_master'].update(cut_count=3,row_count=8,ordered_cuts=[[1,2,3]]*3)
        r['raw_master']['dual_bits']=r['raw_master']['dual_bits'][:5]+[capture.encode(v) for v in [-1e-4,5e-6,-2e-5]]
        r['native_query'].update(filtered_cuts=[[1,2,3]]*2,filtered_sigma_bits=[capture.encode(-1e-4),capture.encode(-2e-5)],raw_cut_indices=[0,2])
        self.assertEqual(self.validate(r)['raw_cut_count'],3)
        bad=copy.deepcopy(r);bad['native_query']['raw_cut_indices']=[2,0]
        with self.assertRaises(capture.CaptureError):self.validate(bad)
        bad=copy.deepcopy(r);bad['native_query']['filtered_sigma_bits'].reverse()
        with self.assertRaises(capture.CaptureError):self.validate(bad)

    def test_nonterminal_and_capture_failure_never_supply_terminal_context(self):
        r={k:copy.deepcopy(self.record[k]) for k in ['schema','identity','entry_policy','source_bcp_log','effective_settings','proof_acceptance']}
        r.update(kind='no_terminal_capture',reason='not_completed_root_only')
        self.assertFalse(self.validate(r)['terminal_eligible'])
        failure={'schema':capture.SCHEMA,'identity':self.identity,'kind':'capture_failure','reason':'native_input_hash_mismatch',
            'phase':4,'call_ordinal':3,'callback_generation':2}
        self.assertFalse(self.validate(failure)['terminal_eligible'])

    def test_partial_extra_and_wrongly_ordered_envelopes(self):
        for envelope in [{'record':self.record,'complete':False},{'record':self.record},
                         {'record':self.record,'complete':True,'extra':1},{'complete':True,'record':self.record}]:
            with self.subTest(keys=list(envelope)),self.assertRaises(capture.CaptureError):self.validate(envelope=envelope)
        with tempfile.NamedTemporaryFile(dir=self.root,delete=False) as f:f.write(b'{"record":{}')
        with self.assertRaises(capture.CaptureError):capture.read_json(Path(f.name),regular=True)
        with tempfile.NamedTemporaryFile(dir=self.root,delete=False) as f:f.write(b'{"complete":true,"complete":true}')
        with self.assertRaises(capture.CaptureError):capture.read_json(Path(f.name),regular=True)

    def test_uint64_nan_and_function_structure(self):
        for value in [-1,1<<64,True,1.,0x7ff8000000000000,0x7ff0000000000000]:
            with self.subTest(value=value),self.assertRaises(capture.CaptureError):capture.bits(value)
        scalar_span=function(left=1,right=1,slope=0,intercept=5)
        scalar_span['cached_image_bits']=scalar_span['pieces'][0]['image_bits']=[capture.encode(2),capture.encode(5)]
        self.assertEqual(capture.function_record(scalar_span,'legacy-span'),[])
        r=copy.deepcopy(self.record);r['labeling_instances']['backward']['arr'][0][0]['piece_count']=8
        with self.assertRaises(capture.CaptureError):self.validate(r)

    def test_root_terminal_controls_and_full_function_tables(self):
        edits=[lambda r:r['entry_policy'].update(finished=False),lambda r:r.update(source_returned_route_count=1),
            lambda r:r['source_bcp_log'].update(nodes_closed=2),lambda r:r.update(processed_node_count=2),
            lambda r:r['raw_master'].update(pricing_node_count=2),lambda r:r['entry_policy'].update(correcting=True),
            lambda r:r['entry_policy'].update(time_budget_milliseconds_bits=capture.encode(51)),
            lambda r:r['effective_settings'].update(eps_bits=capture.encode(1e-6)),
            lambda r:r['native_query'].update(forbidden_arcs=[[1,2]]),lambda r:r['raw_master'].update(row_count=5)]
        for edit in edits:
            r=copy.deepcopy(self.record);edit(r)
            with self.assertRaises(capture.CaptureError):self.validate(r)

    def test_invalid_ids_before_any_demand_access(self):
        class Unindexable:
            def __getitem__(self,key):raise AssertionError('Indexed demands before rejecting original ID')
        from types import SimpleNamespace
        loaded=SimpleNamespace(instance=SimpleNamespace(num_customers=3,demands=Unindexable(),vehicle_capacity=10))
        for path in [[0,1,99,4],[0,-1,4],[0,True,4],[0,1,1,4],[0,4,4]]:
            with self.subTest(path=path),self.assertRaises(capture.CaptureError):paths.check_route(loaded,path,5,[])
        for cut in [[0,1,2],[1,2,5],[1,1,2]]:
            r=copy.deepcopy(self.record);r['raw_master']['ordered_cuts']=[cut]
            with self.assertRaises(capture.CaptureError):self.validate(r)

    def test_partial_route_is_not_full_solution(self):
        context=self.validate()
        with patch('mamut_routing_lib.td.check_td_solution',side_effect=AssertionError('Full-solution checker must not be used')):
            result=paths.inspect_path(context,self.loaded,[0,1,4])
        self.assertEqual(result['checker_route']['customers'],[1])
        self.assertEqual(result['checker_route']['duration'],2.)

    def test_elementarity_capacity_tw_and_forbidden_arcs(self):
        with self.assertRaises(capture.CaptureError):paths.check_route(self.loaded,[0,1,1,4],5,[])
        with self.assertRaises(capture.CaptureError):paths.check_route(self.loaded,[0,1,2,4],5,[[1,2]])
        loaded=copy.deepcopy(self.loaded);loaded.instance.vehicle_capacity=1
        with self.assertRaises(capture.CaptureError):paths.check_route(loaded,[0,1,2,4],5,[])
        loaded=copy.deepcopy(self.loaded);loaded.instance.time_windows[1]=(0.,0.5)
        with self.assertRaises(capture.CaptureError):paths.check_route(loaded,[0,1,4],5,[])

    def test_robust_negative_cost_disagreement_and_near_zero(self):
        context=self.validate();path=[0,1,2,4];original,sha=self.original(context,path,3.)
        good=paths.inspect_path(context,self.loaded,path,reported_native_duration_bits=capture.encode(3.),original_evaluation_path=original,original_evaluation_sha256=sha)
        self.assertTrue(good['robust_raw_master_counterexample_candidate']);self.assertTrue(good['robust_filtered_query_counterexample_candidate'])
        bad=paths.inspect_path(context,self.loaded,path,reported_native_duration_bits=capture.encode(3.5),original_evaluation_path=original,original_evaluation_sha256=sha)
        self.assertTrue(bad['native_cost_disagreement']);self.assertFalse(bad['robust_raw_master_counterexample_candidate'])
        r=copy.deepcopy(self.record);r['raw_master']['dual_bits'][1:3]=[capture.encode(1.5)]*2;r['native_query']['profit_bits']=r['raw_master']['dual_bits'][:5]
        near=self.validate(r);original,sha=self.original(near,path,3.)
        result=paths.inspect_path(near,self.loaded,path,original_evaluation_path=original,original_evaluation_sha256=sha)
        self.assertEqual(result['classification'],'near_zero_numerical_evidence');self.assertFalse(result['robust_raw_master_counterexample_candidate'])
        self.assertEqual(result['filtered_query_reduced_cost_checker']['binary64_fold'],0.)
        self.assertLess(float(result['raw_master_reduced_cost_checker']['exact_binary64_inputs_numerator']),0.)

    def test_stdin_segment_binding_and_explicit_unbound_mode(self):
        bound=self.validate();self.assertEqual(bound['stdin_binding']['status'],'verified')
        with self.assertRaises(capture.CaptureError):self.validate(stdin_sha256='f'*64)
        with self.assertRaises(capture.CaptureError):self.validate(native_segment_offset=0)
        with self.assertRaises(capture.CaptureError):self.validate(native_segment_length=1)
        with self.assertRaises(capture.CaptureError):self.validate(native_segment_sha256='e'*64)
        unbound=self.validate(bind_stdin=False)
        self.assertEqual(unbound['stdin_binding']['status'],'unbound')
        path=[0,1,2,4];original,sha=self.original(unbound,path,3.)
        result=paths.inspect_path(unbound,self.loaded,path,original_evaluation_path=original,original_evaluation_sha256=sha)
        self.assertFalse(result['robust_raw_master_counterexample_candidate'])
        self.assertEqual(result['classification'],'stdin_instance_unbound_uncertified')
        self.assertFalse(result['derivative_event_proof_credit']);self.assertFalse(result['derivative_event_closure_credit'])

    def test_stdin_native_segment_requires_exact_bytes_not_equal_json(self):
        alternate=json.dumps(self.native,indent=2).encode();prefix=json.dumps(self.record['experiment']).encode()+b'\n'
        data=prefix+alternate+b'\n{}\n';type(self).count+=1;path=self.root/f'wrong-stdin-{self.count}.bin';path.write_bytes(data)
        with self.assertRaises(capture.CaptureError):capture.stdin_binding(path,hashlib.sha256(data).hexdigest(),
            len(prefix),len(alternate),hashlib.sha256(alternate).hexdigest(),self.root/'native.json',self.nsha,self.record['experiment'])

    def test_original_evaluator_wrong_binding_is_rejected(self):
        c=self.validate();path=[0,1,2,4];original,sha=self.original(c,path,3.,source_inventory_sha256='0'*64)
        with self.assertRaises(capture.CaptureError):paths.inspect_path(c,self.loaded,path,original_evaluation_path=original,original_evaluation_sha256=sha)

    def test_diagnostic_failure_record_is_not_terminal(self):
        r={'schema':capture.SCHEMA,'kind':'invalid_compose','identity':self.identity,'site':'decreasing_descending_search',
            'outer_index':0,'inner_piece_index':0,'outer_piece_count':0,'inner_piece_count':1,'outer':function(empty=True),'inner':function(),
            'phase':3,'call_ordinal':8,'callback_generation':7}
        c=self.validate(r);self.assertFalse(c['terminal_eligible'])
        r['outer_index']=-1;r['outer_piece_count']=1;r['outer']=function();self.assertFalse(self.validate(r)['terminal_eligible'])


if __name__=='__main__':unittest.main()
