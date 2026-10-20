"""Same report pipeline for interactive reports and scheduled emails."""
import copy
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from api import Pipedrive,Aircall,APIError
from core import (UTMS,field_code,people_rows,tag_catalog,summarize,public_rows,csv_bytes,
                  parse_dt,phone,six_month_boundary,statistic_tags,summarize_all)

def pd_client(settings,progress=None): return Pipedrive(settings['company'],settings['pipedrive_token'],progress)

def fetch_people(settings,options,start,end,progress=None):
    pd=pd_client(settings,progress)
    fields=pd.fields()
    known={field_code(f) for f in fields}
    codes=[v for v in options['utm_fields'].values() if v]
    if not codes: raise ValueError('Map at least one UTM field in Settings.')
    if any(c not in known for c in codes): raise ValueError('A mapped UTM field no longer exists; refresh Settings.')
    tag=options.get('tag_field','label_ids')
    if tag!='label_ids':
        if tag not in known: raise ValueError('The selected tag field no longer exists.')
        codes.append(tag)
    metadata={field_code(f):f for f in fields}
    codes=[c for c in codes if metadata[c].get('is_custom_field',c not in ('label_ids','label','name','add_time','update_time'))]
    people=pd.people(start,end,codes)
    return {'fields':fields,'people':people,'start':start.isoformat(),'end':end.isoformat(),
            'fetched_at':datetime.now(timezone.utc).isoformat(),'options':copy.deepcopy(options),'demo':False,
            'company':pd.company,'timezone':settings.get('timezone','UTC')}

def build(data,options):
    options=copy.deepcopy(options)
    rows,invalid=people_rows(data['people'],data['fields'],options,parse_dt(data['start']),parse_dt(data['end']))
    catalog=tag_catalog(data['fields'],data['people'],options.get('tag_field','label_ids'))
    options['stat_tags']=statistic_tags(catalog,options.get('stat_tags'))
    unknown=(set(options.get('tags',[])) | {t for tokens in options['stat_tags'].values() for t in tokens})-set(catalog)
    # Text-tag values may be absent in a fresh period; preserve their literal meaning.
    missing_ids={t for t in unknown if t.startswith('id:')}
    if missing_ids: raise ValueError('Selected tag IDs are unavailable in the refreshed Pipedrive catalog; review tag settings.')
    for t in unknown: catalog[t]=t.removeprefix('text:')
    return {'rows':rows,'calls':[],'issues':[], 'catalog':catalog, 'invalid_dates':invalid,
            'meta':{**{k:data[k] for k in ('start','end','fetched_at','demo')},
                    **{k:data[k] for k in ('name','company','timezone') if k in data}},'options':copy.deepcopy(options)}

def select_rows(report,groups=None,matched_only=False):
    return [r for r in report['rows'] if (groups is None or r['group_key'] in groups) and (not matched_only or r['matches_tag'])]

def enrich(report,settings,person_ids,start,end,transcripts=True,progress=None,client=None):
    """All phone numbers on selected people; shared numbers in cohort flagged, never guessed."""
    if start<six_month_boundary(datetime.now(timezone.utc)):
        raise ValueError('Aircall exposes six months of call history; choose a more recent activity start.')
    if end<=start: raise ValueError('Activity end must be after start.')
    out=copy.deepcopy(report); progress=progress or (lambda text:None)
    ac=client or Aircall(settings['aircall_id'],settings['aircall_token'],progress)
    all_phones=defaultdict(set)
    for r in out['rows']:
        for p in r['phones']: all_phones[p].add(r['person_id'])
    targets={r['person_id']:r for r in out['rows'] if r['person_id'] in set(person_ids)}
    out['calls']=[c for c in out['calls'] if c['person_id'] not in targets]
    seen=set(c['call_id'] for c in out['calls'])
    for i,(pid,row) in enumerate(targets.items(),1):
        progress(f'Aircall person {i}/{len(targets)}: fetching calls and available transcripts…')
        status='complete'; fetched=[]
        if not row['phones']: status='no valid phone'
        for p in row['phones']:
            if len(all_phones[p])>1:
                status='shared phone — review'
                out['issues'].append({'person_id':pid,'phone':p,'issue':'Phone is shared by multiple people in this report; not attributed.'})
                continue
            raw=ac.calls(p,start.timestamp(),end.timestamp())
            for c in raw:
                cid=str(c['id']); dt=parse_dt(c.get('started_at') or c.get('created_at'))
                if phone(c.get('raw_digits',''))!=p or not dt or not start<=dt<end: continue
                if cid in seen: continue
                seen.add(cid)
                t=ac.transcript(cid) if transcripts else {'status':'not requested','text':'','raw':None}
                answered=parse_dt(c.get('answered_at')); ended=parse_dt(c.get('ended_at'))
                fetched.append({'person_id':pid,'name':row['name'],'utm_group':row['utm_group'],'group_key':row['group_key'],
                    'call_id':cid,'phone':p,'started_at':dt.isoformat(),'direction':c.get('direction',''),
                    'answered':bool(answered),'duration_seconds':c.get('duration',0),
                    'answered_to_end_seconds':max(0,int((ended-answered).total_seconds())) if answered and ended else 0,
                    'agent':(c.get('user') or {}).get('name','Unknown/AI/system'),
                    'aircall_tags':'; '.join(str(t.get('name','')) for t in (c.get('tags') or [])),
                    'comments':'\n'.join(str(x.get('content','')) for x in (c.get('comments') or [])),
                    'transcript_status':t['status'],'transcript':t['text']})
        row.update(activity_start=start.isoformat(),activity_end=end.isoformat(),
                   call_count=len(fetched),answered_calls=sum(c['answered'] for c in fetched),
                   transcripts_available=sum(c['transcript_status']=='available' for c in fetched),aircall_status=status)
        out['calls'].extend(fetched)
    out['meta']['activity_start']=start.isoformat(); out['meta']['activity_end']=end.isoformat()
    out['meta']['aircall_fetched_at']=datetime.now(timezone.utc).isoformat()
    return out

def export_parts(report,groups=None,matched_only=False,include_details=True,include_calls=True,include_transcripts=True):
    cohort=select_rows(report,groups)
    people=[r for r in cohort if not matched_only or r['matches_tag']]
    ids={r['person_id'] for r in people}
    summary=summarize(cohort,report['catalog'],report['options'].get('stat_tags'))
    stats_by_group={r['group_key']:{'group_'+k:r[k] for k in ('people','qualified_people','qualified_pct','contacted_people','contacted_pct')} for r in summary}
    summary=[{'row_type':'utm_group',**{k:v for k,v in r.items() if k!='group_key'}} for r in summary]
    if groups is None and cohort:
        total=summarize_all(cohort,report['catalog'],report['options'].get('stat_tags'))
        summary.insert(0,{'row_type':'total',**{k:v for k,v in total.items() if k!='group_key'}})
    stat_columns=['qualified_people','qualified_pct','contacted_people','contacted_pct']
    parts={'utm_summary.csv':csv_bytes(summary,['row_type','utm_group','people','matching_people','match_pct']+stat_columns+['aircall_people_fetched','calls']+list(dict.fromkeys(k for r in summary for k in r if k.startswith('tag:'))))}
    if include_details:
        detail=[{**public,**stats_by_group[row['group_key']]} for row,public in zip(people,public_rows(people))]
        parts['people.csv']=csv_bytes(detail,list(detail[0]) if detail else ['person_id','name','utm_group','matches_tag','group_people']+['group_'+k for k in stat_columns])
    calls=[{k:v for k,v in c.items() if k!='group_key' and (include_transcripts or k!='transcript')} for c in report['calls'] if c['person_id'] in ids]
    if include_calls:
        parts['aircall_activity.csv']=csv_bytes(calls,list(calls[0]) if calls else ['person_id','call_id','transcript_status'])
        if include_transcripts:
            for c in calls:
                if c.get('transcript'): parts[f'transcripts/call_{c["call_id"]}.txt']=c['transcript'].encode('utf-8')
    metadata={'version':2,'report':report['meta'],'options':report['options'],'selected_groups':groups,
              'matched_people_only':matched_only,'summary_denominator':'All UTM people in selected groups, regardless of detail filter',
              'tag_meaning':'Current Pipedrive labels/custom tags at fetch time, not historical conversion events',
              'statistics':'Qualified/contacted count any configured tag once per person. Percentages use all people in the group; blank means no statistic tag configured.',
              'activity_requested':bool(report['meta'].get('aircall_fetched_at')),
              'issues':[issue for issue in report['issues'] if str(issue.get('person_id','')) in {r['person_id'] for r in cohort}]}
    parts['report_settings.json']=json.dumps(metadata,indent=2,ensure_ascii=False).encode('utf-8')
    return parts

def zip_parts(parts):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,value in parts.items(): z.writestr(name,value)
    return out.getvalue()


def transcript_filename(call,person):
    """Use the person's full name and Aircall ID; never permit ZIP path traversal."""
    def safe(value):
        return re.sub(r'[^\w-]+','_',unicodedata.normalize('NFKC',str(value))).strip('_-')
    name=safe(person.get('name') or call.get('name') or '')[:140]
    if not name: name='Unknown_Person_'+safe(person.get('person_id','unknown'))
    call_id=safe(call['call_id'])[:60] or 'unknown'
    return f'transcripts/{name}_{call_id}.txt'


def full_report_parts(report,groups=None,created_at=None,transcript_search=None):
    """A full bundle always contains both all-people and matched-only datasets.

    The on-screen matching-only checkbox is deliberately not an argument: it
    filters the standalone people CSV, never the all_People.csv in this bundle.
    """
    from pdf_summary import overall_summary_pdf
    cohort=select_rows(report,groups)
    matching=[r for r in cohort if r['matches_tag']]
    ids={r['person_id'] for r in cohort}; matched_ids={r['person_id'] for r in matching}
    all_parts=export_parts(report,groups,False,include_transcripts=False)
    match_parts=export_parts(report,groups,True,include_transcripts=False)
    parts={}
    if cohort:
        parts['all_People.csv']=all_parts['people.csv']
        parts['UTM_summary.csv']=all_parts['utm_summary.csv']
    if matching: parts['Match_people.csv']=match_parts['people.csv']
    calls=[{k:v for k,v in c.items() if k!='group_key'} for c in report['calls'] if c['person_id'] in ids]
    match_calls=[c for c in calls if c['person_id'] in matched_ids]
    if calls: parts['Aircall_Activity.csv']=csv_bytes(calls)
    if match_calls: parts['Match_aircall_activity.csv']=csv_bytes(match_calls)
    parts['Overall Summary.pdf']=overall_summary_pdf(report,cohort,groups,created_at)
    by_id={r['person_id']:r for r in cohort}
    for call in calls:
        if str(call.get('transcript') or '').strip():
            name=transcript_filename(call,by_id[call['person_id']])
            parts[name]=call['transcript'].encode('utf-8')
    if transcript_search is not None:
        from transcript_search import search_export_parts
        parts.update(search_export_parts(calls,transcript_search,
                     {**report['meta'],'selected_groups':groups,'scope':'Fetched calls for this UTM export'}))
    return parts

