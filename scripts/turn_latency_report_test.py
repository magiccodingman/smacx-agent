from turn_latency_report import report

def event(i,t,kind,**payload):
 return {'event_id':str(i),'recorded_unix':t,'correlation':{'run_id':'r','request_id':'q'},'payload':{'type':kind,**payload}}
rows=[event(1,1,'response_started'),event(2,2,'response_delta',chunks=[{'choices':[{'delta':{'role':'assistant'}}]}]),
      event(3,4,'response_delta',chunks=[{'choices':[{'delta':{'content':'hello'}}]}]),
      event(4,7,'response_finished',complete=True)]
r=report(rows+rows,0,10)
assert r['completed_responses']==1 and r['provider']['sum_seconds']==6
assert r['time_to_first_content']['sum_seconds']==3 and r['content_to_finish']['sum_seconds']==3
assert r['cached_tokens'] is None and r['prompt_tokens'] is None
assert report(rows,2,10)['partial_responses']==1
assert report(rows,0,6)['partial_responses']==1
assert not r['complete_faction_turn_verified']
print('timing report: deduplicated events, content-only TTFT, partial windows and unavailable usage passed')
