"""Shared stored-witness and global-bound consistency checks for ms58 audits."""
from __future__ import annotations

import math

from ms58_analyze_pairs import EXPECTED_CHECKER
from probe_runner import EVENT_TYPES, digest

CONSISTENCY_ABSOLUTE_TOLERANCE = 1e-6


def proof_evidence(validation, events, horizon, late_events=(), *, expected_inputs):
    """Bind every checked candidate to its accepted event before cross-run use."""
    if validation.get('checker') != EXPECTED_CHECKER:
        raise ValueError('Proof-consistency evidence uses an unpinned checker')
    keys = ['instance_name', 'instance_sha256', 'atf_sha256']
    if any(key not in expected_inputs or validation.get(key) != expected_inputs[key] for key in keys):
        raise ValueError('Checked validation payload is bound to another input')
    by_sequence = {e['sequence']: e for e in events}
    candidates = {}
    for candidate in validation.get('candidates', []):
        if candidate.get('valid') is not True:
            continue
        event = by_sequence.get(candidate['sequence'], {})
        cost = candidate.get('checked_cost')
        if (event.get('event') != 'candidate' or event.get('routes') != candidate.get('routes')
                or event.get('elapsed_s') != candidate.get('elapsed_s')
                or not 0 <= candidate['elapsed_s'] <= horizon
                or isinstance(cost, bool) or not isinstance(cost, (int, float))
                or not math.isfinite(cost) or cost < 0):
            raise ValueError('Checked candidate is not bound to a valid in-budget event')
        candidates[candidate['sequence']] = {key: candidate[key] for key in
                                           ['sequence', 'elapsed_s', 'checked_cost']}
        candidates[candidate['sequence']]['routes_sha256'] = digest(candidate['routes'])
    bounds, unavailable_bounds, closures, preceding = [], [], [], None
    for event_list, scope in [(events, 'in_budget'), (late_events, 'after_horizon')]:
        for event in event_list:
            bound = event.get('global_lb')
            if isinstance(bound, float) and not math.isfinite(bound):
                bound = repr(bound)
            if 'global_lb' in event:
                evidence = {**{key: event.get(key) for key in ['sequence', 'elapsed_s', 'event']},
                            'global_lb': bound, 'timing_scope': scope}
                # The pinned bridge emits null for its unset lower-bound sentinel
                # on search termination. This phase reports no numeric bound.
                # Null bound/closure events and every other malformed claim
                # remain in bounds for the existing strict consistency checks.
                if (bound is None and event.get('event') == 'phase'
                        and event.get('phase') == 'exact_terminated'
                        and event.get('source') == 'search_terminated'):
                    unavailable_bounds.append({**evidence, 'phase': event['phase'], 'source': event['source'],
                        'reason': 'search_terminated_without_global_lower_bound'})
                else:
                    bounds.append(evidence)
            if event.get('event') == 'candidate':
                preceding = candidates.get(event['sequence']) if scope == 'in_budget' else None
            elif event.get('event') == 'closure':
                closures.append({'sequence': event['sequence'], 'elapsed_s': event['elapsed_s'],
                    'global_lb': bound, 'status': event.get('status'), 'timing_scope': scope,
                    'preceding_checked_candidate': preceding})
    return {'checked_candidates': list(candidates.values()), 'witness_evidence_audited': True,
            'global_bound_claims': bounds, 'unavailable_global_bound_events': unavailable_bounds,
            'raw_closure_claims': closures}


def validate_late_event_identity(events, identity):
    last = identity['horizon_s']
    for sequence, event in enumerate(events):
        elapsed = event.get('elapsed_s')
        if (event.get('run_id') != identity['run_id'] or event.get('manifest_hash') != identity['manifest_hash']
                or event.get('schema_version') != identity['schema_version']
                or event.get('sequence') != sequence or event.get('event') not in EVENT_TYPES
                or event.get('excluded_reason') != 'after_horizon'
                or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or not elapsed > identity['horizon_s'] or elapsed < last):
            raise ValueError('Late event identity, ordering or horizon classification mismatch')
        last = elapsed


def apply_feasible_consistency(cells):
    """Retrospectively test proof claims against all known checked feasible costs.

    Feasible witnesses from a run with contradictory bounds remain feasible.
    Quality references stay frozen. This minimum is only a proof consistency
    ceiling, not a new quality target or a certified lower bound.
    """
    ceilings, identities = {}, {}
    for cell in cells:
        key = cell['instance_key']
        identity = (cell['instance_sha256'], cell['atf_sha256'], cell['reference_cost'])
        if key in identities and identities[key] != identity:
            raise ValueError('Cross-run proof audit would mix different inputs or frozen references')
        identities[key] = identity
        if key not in ceilings:
            ceilings[key] = {'cost': cell['reference_cost'], 'source': 'frozen_checked_reference',
                'reference_manifest_sha256': cell['reference_manifest_sha256']}
        if cell.get('witness_evidence_audited'):
            for candidate in cell.get('checked_candidates', []):
                if candidate['checked_cost'] < ceilings[key]['cost']:
                    ceilings[key] = {'cost': candidate['checked_cost'], 'source': 'checked_in_budget_candidate',
                        'task_id': cell['task_id'], 'run_id': cell.get('run_id'), **candidate}
    for cell in cells:
        ceiling = ceilings[cell['instance_key']]
        contradictions, unsupported = [], []
        for claim in cell.get('global_bound_claims', []):
            bound = claim['global_lb']
            malformed = isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(bound)
            if malformed or bound > ceiling['cost'] + CONSISTENCY_ABSOLUTE_TOLERANCE:
                contradictions.append({'kind': 'invalid_global_bound' if malformed else 'global_bound_above_feasible_cost',
                    **claim, 'excess': None if malformed else bound - ceiling['cost']})
        for claim in cell.get('raw_closure_claims', []):
            witness = claim.get('preceding_checked_candidate')
            bound = claim.get('global_lb')
            bound_is_finite = (not isinstance(bound, bool) and isinstance(bound, (int, float))
                               and math.isfinite(bound))
            if not witness or not bound_is_finite:
                unsupported.append({'kind': 'closure_missing_checked_witness' if not witness else
                    'closure_missing_finite_lower_bound', 'sequence': claim['sequence'],
                    'elapsed_s': claim['elapsed_s'], 'timing_scope': claim.get('timing_scope')})
            elif abs(bound - witness['checked_cost']) > CONSISTENCY_ABSOLUTE_TOLERANCE:
                contradictions.append({'kind': 'closure_lower_bound_differs_from_checked_incumbent',
                    'sequence': claim['sequence'], 'elapsed_s': claim['elapsed_s'], 'global_lb': bound,
                    'checked_cost': witness['checked_cost'], 'bound_minus_checked': bound - witness['checked_cost']})
            if witness and witness['checked_cost'] > ceiling['cost'] + CONSISTENCY_ABSOLUTE_TOLERANCE:
                contradictions.append({'kind': 'closure_incumbent_above_feasible_cost',
                    'sequence': claim['sequence'], 'elapsed_s': claim['elapsed_s'],
                    'checked_cost': witness['checked_cost'], 'excess': witness['checked_cost'] - ceiling['cost']})
            if str(claim.get('status', '')).lower() == 'infeasible':
                contradictions.append({'kind': 'infeasible_closure_with_known_feasible_witness',
                    'sequence': claim['sequence'], 'elapsed_s': claim['elapsed_s']})
        blocking_unsupported = [claim for claim in unsupported if claim.get('timing_scope') != 'after_horizon']
        cell.setdefault('raw_reported_closure_s', cell.get('reported_closure_s'))
        cell['consistency_audit'] = {'status': 'contradicted' if contradictions else
                'unsupported' if blocking_unsupported else 'consistent' if cell.get('witness_evidence_audited') else 'unavailable',
            'absolute_tolerance': CONSISTENCY_ABSOLUTE_TOLERANCE, 'feasible_ceiling': ceiling,
            'global_bound_claims_checked': len(cell.get('global_bound_claims', [])),
            'closure_claims_checked': len(cell.get('raw_closure_claims', [])),
            'contradictions': contradictions,
            'unsupported_closure_claims': unsupported,
            'unsupported_in_budget_closure_claims': blocking_unsupported,
            'meaning': 'Necessary retrospective consistency check, not independent proof certification.'}
        if contradictions or blocking_unsupported:
            cell['observation_usable'] = False
            cell['reported_closure_s'] = None
            cell['capped_reported_closure_s'] = cell['horizon_s']
            cell['reported_closure_with_native_guard_warning'] = False
        baseline_usable = (cell.get('audit_ok', False) and cell.get('finished_task', False)
            and cell.get('raw_result_status', cell.get('status')) in ['completed', 'deadline']
            and not cell.get('resource_failure_kind') and not cell.get('result_issues'))
        cell['proof_observation_usable'] = (cell.get('witness_evidence_audited') is True
            and cell.get('observation_usable', baseline_usable) and not contradictions and not blocking_unsupported)
    return ceilings


