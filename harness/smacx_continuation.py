"""Conservative wire-only continuation: preserve intent verbatim, never infer a summary."""
import copy
import json


def preserve_continuation(messages, last_user, tool_names, decode, *, threshold=24000, protected=()):
    """Prune settled old protocol pairs; retain unresolved/unknown evidence and prose.

    Recent episode is complete. Earlier assistant prose is lossless except exact
    duplicates under pressure. Tool pairing is preserved even for mixed batches.
    Durable history is never modified. No semantic fact or confidence is invented.
    """
    rows=copy.deepcopy(messages)
    boundaries=[i for i,m in enumerate(rows) if m.get('role')=='user']
    cutoff=boundaries[-2] if len(boundaries)>1 else 0
    results={str(m.get('tool_call_id')):decode(m.get('content'))
             for m in rows if m.get('role')=='tool'}
    compacted = 0
    for index, message in enumerate(rows):
        ident = str(message.get('tool_call_id') or '')
        result = results.get(ident)
        if message.get('role') != 'tool' or ident in protected or tool_names.get(ident) != 'smac_execute_choice':
            continue
        if not isinstance(result, dict):
            continue
        result = copy.deepcopy(result)
        nested = (result.get('post_action_decision') or {}).get('frame') or {}
        obsolete = index < last_user or nested.get('decision_consumed') or nested.get('superseded_runtime_state')
        if obsolete:
            result.pop('post_action_decision', None)
            # Only historic catalogs expire; failure evidence and obligations remain.
            recovery = result.get('recovery')
            if index < last_user and isinstance(recovery, dict) and 'frame' in recovery:
                recovery['frame'] = {'superseded_runtime_state': True}
        if index < last_user:
            for key in ('decision_id', 'choice_id', 'decision_consumed'):
                result.pop(key, None)
            guidance = result.get('required_next') or {}
            if not guidance.get('stop_after') and guidance.get('tool') in {'smac_decision', 'smac_execute_choice'}:
                result.pop('required_next', None)
        if obsolete and result.get('ok') is True and result.get('execution_status') == 'completed':
            # Unknown fields can contain effect-verification obligations. Remove
            # only known expired protocol handles; preserve all outcome fields.
            result = {key: value for key, value in result.items() if key not in {
                'decision_id', 'choice_id', 'decision_consumed', 'required_next'}}
            result['semantic_gc'] = 'historical_execution_receipt'
        # Decode redundant transport escaping, retaining all unknown outcome fields.
        message['content'] = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
        results[ident] = result
        compacted += 1
    removable=set()
    for i,m in enumerate(rows):
        if i>=last_user or m.get('role')!='assistant':continue
        for call in m.get('tool_calls') or []:
            ident=str(call.get('id'));r=results.get(ident);name=tool_names.get(ident)
            if not isinstance(r,dict) or r.get('ok') is not True:continue
            # Query evidence can contain strategic dependencies. Retain it unless
            # the existing semantic layer explicitly marked it superseded.
            settled=r.get('superseded_runtime_state') or r.get('semantic_gc')=='superseded_query_evidence'
            if i>=cutoff and not settled:continue
            if name=='smac_execute_choice':
                settled=False  # Keep the compact outcome and its journal reference.
            if any(r.get(k) for k in ('queued','persistent','order','gameplay_mutations_blocked','incident')):
                settled=False
            if settled:removable.add(ident)
    filtered=[]
    for m in rows:
        if m.get('role')=='tool' and str(m.get('tool_call_id')) in removable:continue
        if m.get('role')=='assistant' and m.get('tool_calls'):
            m['tool_calls']=[c for c in m['tool_calls'] if str(c.get('id')) not in removable]
            if not m['tool_calls']:
                m.pop('tool_calls')
                if not m.get('content'):continue
        filtered.append(m)
    # Exact dedup only within historical standalone prose; keep newest occurrence.
    # Never summarize competing interpretations or remove recent handoffs.
    duplicates=0
    if len(json.dumps(rows,ensure_ascii=False))>threshold:
        old_ids={id(m) for m in rows[:cutoff]};seen=set();dedup=[]
        for m in reversed(filtered):
            text=m.get('content')
            eligible=id(m) in old_ids and m.get('role')=='assistant' and not m.get('tool_calls') and isinstance(text,str) and bool(text)
            if eligible and text in seen:
                duplicates+=1;continue
            if eligible:seen.add(text)
            dedup.append(m)
        filtered=list(reversed(dedup))
    return filtered,{'settled_protocol_pairs_removed':len(removable),
                     'execution_receipts_compacted':compacted,
                     'exact_prose_duplicates_removed':duplicates,
                     'recent_episode_retained':True,'semantic_summary_inferred':False}
