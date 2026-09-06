"""Human summaries preserve receipt semantics and label uncertain attribution."""
from __future__ import annotations
import json
import re
from collections import Counter


def failure_code(error):
    """Normalize known transport/dispatcher forms without guessing their cause."""
    if isinstance(error, dict):
        return str(error.get('code', 'structured_error'))[:160]
    value = str(error or '')
    # These are the pinned Hermes dispatcher's pre-invocation failures. Keep
    # raw messages in the event, but do not use parameter/schema text as keys.
    if re.match(r"^tool_call to '[^']+' is missing required argument\(s\):", value):
        return 'schema_missing_required'
    if re.match(r"^tool_call cannot invoke '[^']+' \(it is itself a bridge tool\)", value):
        return 'bridge_tool_not_invocable'
    if re.fullmatch(r'[a-z][a-z0-9_]{0,159}', value):
        return value
    return 'unclassified_error_text'


def result_object(value):
    for _ in range(5):
        if isinstance(value, str):
            try:value=json.loads(value)
            except ValueError:
                decoder=json.JSONDecoder()
                found=None
                offsets=(i for i,c in enumerate(value) if c=='{')
                for _,offset in zip(range(32),offsets):
                    try:candidate,_=decoder.raw_decode(value[offset:])
                    except ValueError:continue
                    if isinstance(candidate,dict) and any(k in candidate for k in ('ok','error','result','content')):
                        found=candidate;break
                if found is None:return {"text":value}
                value=found
        elif isinstance(value,dict) and 'ok' not in value and isinstance(value.get('result'),(str,dict)):
            value=value['result']
        elif isinstance(value,dict) and isinstance(value.get('error'),str):
            # Hermes serializes MCP isError text inside an outer error string.
            # Only unwrap a declared failure; an inner success cannot erase it.
            try:nested=json.loads(value['error'])
            except ValueError:break
            if not isinstance(nested,dict) or nested.get('ok') is not False or not nested.get('error'):
                break
            value=nested
        elif isinstance(value, dict) and isinstance(value.get('content'),list):
            texts=[r.get('text') for r in value['content'] if isinstance(r,dict) and r.get('type')=='text']
            if len(texts)!=1:return value
            value=texts[0]
        else:break
    return value if isinstance(value,dict) else {"value":value}


def summary(event):
    kind=event.get('kind','unknown');payload=event.get('payload') or {}
    tool=payload.get('managed_name') or payload.get('tool') or ''
    if kind=='retained_message':
        return f"retained {payload.get('role','')} {payload.get('tool_name') or ''}: {payload.get('content') or ''}"
    if kind=='sovereign_response':
        message=payload.get('message') or {}
        return str(message.get('content') or '') if isinstance(message,dict) else str(message)
    if kind=='tool_validation_rejected':
        return f"{tool} rejected before execution {json.dumps(payload,ensure_ascii=False,separators=(',',':'))}"
    if kind in {'tool_requested','managed_tool_started'}:
        arguments=payload.get('arguments') or {}
        if isinstance(arguments,dict) and isinstance(arguments.get('arguments'),dict):arguments=arguments['arguments']
        return f"{tool} request {json.dumps(arguments,ensure_ascii=False,separators=(',',':'))}"
    if kind in {'tool_returned','managed_tool_returned','managed_tool_validation_rejected'}:
        result=result_object(payload.get('result',payload.get('content')))
        chosen={k:result[k] for k in ('ok','kind','error','phase','focus','executed_choice',
            'native_action_executed','execution','execution_status','decision_consumed',
            'completed','queued','action_id','effect_disposition','state_changed_during_enumeration',
            'turn_handoff_required','turn_boundary_notice','choice_scope','required_next','persistence','journal_event_id',
            'energy_cost','energy_credits','minerals_added','minerals_accumulated','production_name') if k in result}
        if isinstance(result.get('choices'),list):
            chosen['choices']=[{k:r[k] for k in ('choice_id','label','name','may_close_turn','energy_cost','mineral_cost','production_name') if k in r}
                for r in result['choices'][:12] if isinstance(r,dict)]
            chosen['more_choices']=max(0,len(result['choices'])-12)
        if not chosen:chosen={'text':result.get('text',result)}
        return f"{tool} -> {json.dumps(chosen,ensure_ascii=False,separators=(',',':'))}"
    if kind=='choice_selected':
        return f"selected {payload.get('label')} focus={json.dumps(payload.get('focus_before'))} choice={json.dumps(payload.get('choice'))}"
    if kind=='journal_event':
        event_type=payload.get('event_type','')
        if event_type.startswith(('memory.','attention.','specialist.','game.action','checkpoint','recovery')):
            return f"journal {event_type} turn={payload.get('turn')} {json.dumps(payload.get('payload',{}),ensure_ascii=False)}"
    if kind in {'capture_gap','provider_transport_failed','managed_tool_exception','runtime_context_failed','tool_batch_finished','history_compaction','control_operation_failed','control_operation_deferred','worker_liveness_lost'}:
        return kind+' '+json.dumps(payload,ensure_ascii=False)
    return ''


class Metrics:
    def __init__(self):
        self.events=Counter();self.tools=Counter();self.failures=Counter();self.actors=Counter()
        self.streams_finished=0;self.streams_incomplete=0;self.latencies={}
        self.requests=set();self.terminal_requests=set()
    def add(self,event):
        kind=event.get('kind','unknown');payload=event.get('payload') or {}
        self.events[kind]+=1;self.actors[event.get('actor','unknown')]+=1
        request_id=(event.get('correlation') or {}).get('request_id')
        if request_id:
            if kind=='provider_request_submitted':self.requests.add(request_id)
            if kind in {'provider_response_body','provider_response_stream','provider_transport_failed'}:
                self.terminal_requests.add(request_id)
        if kind in {'tool_requested','tool_validation_rejected'}:self.tools[payload.get('managed_name','unknown')]+=1
        if kind=='tool_validation_rejected':self.failures[kind+':unknown_tool_name']+=1
        if kind=='control_operation_failed':self.failures[kind+':'+str(payload.get('error_code','unknown'))]+=1
        if kind in {'tool_returned','managed_tool_returned','managed_tool_validation_rejected'}:
            result=result_object(payload.get('result',payload.get('content')))
            error=result.get('error')
            if error:
                self.failures[kind+':'+failure_code(error)]+=1
            elif result.get('isError') or result.get('ok') is False:
                self.failures[kind+':unclassified_failure']+=1
        if kind in {'provider_transport_failed','managed_tool_exception','runtime_context_failed','capture_gap'}:
            error=payload.get('error') or payload.get('reason')
            code=failure_code(error) if error else str(payload.get('exception_type','unspecified'))
            self.failures[kind+':'+code]+=1
        if kind=='provider_response_stream':
            if payload.get('done_marker_observed') and not payload.get('capture_truncated'):self.streams_finished+=1
            else:self.streams_incomplete+=1
        duration=payload.get('elapsed_ms')
        if isinstance(duration,(int,float)):
            key=kind+':'+str(payload.get('tool',''))
            row=self.latencies.setdefault(key,{'count':0,'total_ms':0,'max_ms':0})
            row['count']+=1;row['total_ms']+=duration;row['max_ms']=max(row['max_ms'],duration)
    def as_dict(self):
        pending=sorted(self.requests-self.terminal_requests)
        return {'event_counts':dict(self.events),'actor_counts':dict(self.actors),
            'provider_requests_without_terminal_capture':{'count':len(pending),
                'request_ids':pending[:64],'more':max(0,len(pending)-64),
                'meaning':'May be in flight, interrupted, or missing capture; not automatically a provider failure.'},
            'sovereign_requested_tool_counts':dict(self.tools),'failure_observations_by_layer':dict(self.failures),
            'failure_counts_are_not_deduplicated_incidents':True,
            'provider_streams_with_done_marker':self.streams_finished,
            'provider_streams_incomplete_or_truncated':self.streams_incomplete,'latency_by_layer':self.latencies,
            'model_quality_or_causal_use_not_inferred':True}
