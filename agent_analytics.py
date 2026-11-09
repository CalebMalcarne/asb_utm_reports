"""Per-agent call activity and verified first-response attribution."""
import copy
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime,timezone
from statistics import fmean

from core import csv_bytes,parse_dt,phone
from service import select_rows,transcript_filename
from callback_metrics import duration_label

SORTS={'Calls made':'calls_made','Average response time':'average_response_seconds',
       'Workday response time':'workday_average_seconds','After-hours response time':'after_hours_average_seconds'}


def agent_key(call):
    return 'id:'+str(call['agent_id']) if call.get('agent_id') else 'unknown'


def safe_agent_folder(key):
    return 'agent_'+hashlib.sha256(key.encode()).hexdigest()[:16]


def _seconds(value):
    return float(value) if isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None


def _rank(rows,field,rank_field,descending=False):
    eligible=[r for r in rows if r['agent_key']!='unknown' and r[field] is not None
              and (field!='calls_made' or r[field]>0)]
    values=sorted([r[field] for r in eligible],reverse=descending)
    for row in rows:
        row[rank_field]=values.index(row[field])+1 if row in eligible else None
        row[rank_field+'_of']=len(values)


def analyze_agents(dataset,report=None,sort_by='Calls made'):
    if sort_by not in SORTS: raise ValueError('Choose a valid agent sort order.')
    if report is not None and bool(report['meta'].get('demo'))!=bool(dataset['meta'].get('demo')):
        raise ValueError('Demo calls and live Pipedrive leads cannot be mixed.')
    start,end=parse_dt(dataset['meta']['start']),parse_dt(dataset['meta']['end'])
    people=select_rows(report) if report is not None else []
    owners=defaultdict(list)
    for person in people:
        for number in set(filter(None,(phone(p) for p in person['phones']))): owners[number].append(person)
    first_calls=defaultdict(list)
    for person in people:
        if person.get('first_call_id'): first_calls[str(person['first_call_id'])].append(person)
    calls=[]; seen=set(); credited=set()
    for raw in sorted(dataset['calls'],key=lambda c:(c['started_at'],str(c['call_id']))):
        cid=str(raw['call_id']); when=parse_dt(raw['started_at'])
        if cid in seen or not when or not start<=when<end: continue
        seen.add(cid)
        call={**raw,'call_id':cid,'agent_key':agent_key(raw),'person_id':'','person_created_at':None,
              'lead_submission_type':'Unknown','response_included':False,'response_seconds':None,
              'response_time':'—','response_status':'','response_match':'No matching loaded Pipedrive lead'}
        matches=owners.get(phone(raw.get('phone')),[])
        if len(matches)>1:
            call['response_match']='Shared phone; response excluded'
        elif len(matches)==1:
            p=matches[0]
            call.update(person_id=p['person_id'],person_created_at=p['created_at'],
                        lead_submission_type={'Yes':'After-hours','No':'Workday'}.get(p.get('after_hours_lead'),'Unknown'))
            if raw.get('direction')!='outbound': reason='Inbound call; not a first outbound response'
            elif len(first_calls[cid])!=1 or str(p.get('first_call_id'))!=cid:
                reason='Follow-up / first response outside period' if p.get('first_call_id') else 'First response not verified'
            elif parse_dt(p.get('first_called_at'))!=when: reason='First-call timestamp mismatch; excluded'
            elif p['person_id'] in credited: reason='First response already counted'
            else:
                field='after_hours_response_seconds' if call['lead_submission_type']=='After-hours' else 'speed_to_lead_seconds'
                seconds=_seconds(p.get(field))
                if seconds is None or call['lead_submission_type']=='Unknown': reason='Response duration not verified'
                else:
                    credited.add(p['person_id']); reason='Verified first outbound response'
                    call.update(response_included=True,response_seconds=seconds,response_time=duration_label(seconds),
                                response_status=p.get('response_status',''))
            call['response_match']=reason
        call['call_duration']=duration_label(_seconds(raw.get('duration_seconds')))
        call['answered_to_end']=duration_label(_seconds(raw.get('answered_to_end_seconds')))
        calls.append(call)
    grouped=defaultdict(list)
    for call in calls: grouped[call['agent_key']].append(call)
    agents=[]
    for key,items in grouped.items():
        replies=[c for c in items if c['response_included']]
        work=[c['response_seconds'] for c in replies if c['lead_submission_type']=='Workday']
        after=[c['response_seconds'] for c in replies if c['lead_submission_type']=='After-hours']
        durations=[v for c in items if (v:=_seconds(c.get('duration_seconds'))) is not None]
        talk=[v for c in items if c['answered'] and (v:=_seconds(c.get('answered_to_end_seconds'))) is not None]
        available=sum(bool(str(c.get('transcript') or '').strip()) for c in items)
        agents.append(dict(agent_key=key,agent_id=items[-1].get('agent_id',''),
            agent=items[-1]['agent'] if key!='unknown' else 'Unknown / system (no agent ID)',
            total_calls=len(items),calls_made=sum(c['direction']=='outbound' for c in items),
            calls_received=sum(c['direction']=='inbound' for c in items),answered_calls=sum(c['answered'] for c in items),
            unanswered_calls=sum(not c['answered'] for c in items),
            total_call_seconds=sum(durations),average_call_seconds=fmean(durations) if durations else None,
            total_answered_to_end_seconds=sum(talk),average_answered_to_end_seconds=fmean(talk) if talk else None,
            workday_responses=len(work),after_hours_responses=len(after),verified_responses=len(replies),
            workday_average_seconds=fmean(work) if work else None,after_hours_average_seconds=fmean(after) if after else None,
            average_response_seconds=fmean(work+after) if work or after else None,
            available_transcripts=available,pending_transcripts=sum(c.get('transcript_status')=='not requested' for c in items)))
    for field,rank,desc in [('calls_made','calls_rank',True),('average_response_seconds','response_rank',False),
                          ('workday_average_seconds','workday_response_rank',False),('after_hours_average_seconds','after_hours_response_rank',False)]:
        _rank(agents,field,rank,desc)
    field=SORTS[sort_by]
    agents.sort(key=lambda a:((a['agent_key']=='unknown'),
        -a[field] if field=='calls_made' else (a[field] if a[field] is not None else float('inf')),
        a['average_response_seconds'] if a['average_response_seconds'] is not None else float('inf'),a['agent'].casefold(),a['agent_key']))
    for a in agents:
        for raw,label in [('average_response_seconds','average_response'),('workday_average_seconds','workday_average'),
                          ('after_hours_average_seconds','after_hours_average'),('total_call_seconds','total_call_time'),
                          ('average_call_seconds','average_call_time'),('total_answered_to_end_seconds','total_answered_to_end'),
                          ('average_answered_to_end_seconds','average_answered_to_end')]: a[label]=duration_label(a[raw])
    return dict(agents=agents,calls=calls,meta={**copy.deepcopy(dataset['meta']),
        'sort_by':sort_by,'response_source':copy.deepcopy(report['meta']) if report is not None else None,
        'response_cohort_people':len(people),'verified_responses':len(credited),
        'calls_without_response_measurement':len(calls)-sum(c['response_included'] for c in calls),
        'ranking_rules':'Calls made ranks outbound counts descending; response ranks means ascending. Ties share competition rank. Missing response samples and missing agent IDs are unranked.',
        'response_rules':'Only the verified first outbound call for each loaded Pipedrive UTM lead is credited to its Aircall user ID, if that call is in the selected call period. Follow-ups, inbound calls, shared phones and unverified matches are excluded from response averages, but retained in call totals.',
        'duration_rules':'Call duration is Aircall duration. Answered-to-end time may include hold time; it is not a speech-only measure.'})


def report_parts(analysis,key=None):
    from agent_pdf import agent_report_pdf
    agents=[a for a in analysis['agents'] if key is None or a['agent_key']==key]
    if key is not None and not agents: raise ValueError('Agent is absent from this loaded period.')
    calls=[c for c in analysis['calls'] if key is None or c['agent_key']==key]
    zone=analysis['meta']['timezone']
    parts={'Agent_Report.pdf':agent_report_pdf(analysis,key)}
    if agents: parts['Agent_rankings.csv']=csv_bytes(agents)
    if calls: parts['Calls.csv']=csv_bytes([{k:v for k,v in c.items() if k!='transcript'} for c in calls],zone=zone)
    replies=[c for c in calls if c['response_included']]
    if replies: parts['Lead_response_times.csv']=csv_bytes([{k:v for k,v in c.items() if k!='transcript'} for c in replies],zone=zone)
    parts['Report_settings.json']=json.dumps({**analysis['meta'],'exported_agent_key':key,
        'created_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2).encode()
    return parts


def transcript_parts(analysis,key=None):
    calls=[c for c in analysis['calls'] if key is None or c['agent_key']==key]
    parts={}
    for c in calls:
        if str(c.get('transcript') or '').strip():
            parts[safe_agent_folder(c['agent_key'])+'/'+transcript_filename(c,c)]=c['transcript'].encode()
    if calls:
        parts['Transcript_manifest.csv']=csv_bytes([{k:c.get(k) for k in ('agent_id','agent','call_id','name','started_at','transcript_status')} for c in calls],zone=analysis['meta']['timezone'])
    return parts
