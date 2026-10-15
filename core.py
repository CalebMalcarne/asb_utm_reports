import calendar
import csv
import io
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import phonenumbers

UTC=timezone.utc
UTMS=['utm_source','utm_campaign','utm_medium','utm_content','utm_term']
PRESETS=['Today','Yesterday','This week','Last week','Last 7 days','This month','Last month','Last 30 days','Custom']

def parse_dt(value):
    if value is None or value=='': return None
    try:
        if isinstance(value,(int,float)): return datetime.fromtimestamp(value,UTC)
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)
    except (ValueError,TypeError,OverflowError): return None

def period(preset, zone, now=None, custom=None, days=7):
    now=now or datetime.now(UTC)
    today=now.astimezone(ZoneInfo(zone)).date()
    monday=today-timedelta(days=today.weekday())
    first=today.replace(day=1)
    if preset=='Today': a,b=today,today
    elif preset=='Yesterday': a=b=today-timedelta(days=1)
    elif preset=='This week': a,b=monday,today
    elif preset=='Last week': a,b=monday-timedelta(days=7),monday-timedelta(days=1)
    elif preset=='Last 7 days': a,b=today-timedelta(days=6),today
    elif preset=='This month': a,b=first,today
    elif preset=='Last month': b=first-timedelta(days=1); a=b.replace(day=1)
    elif preset=='Last 30 days': a,b=today-timedelta(days=29),today
    elif preset=='Previous N complete days': a,b=today-timedelta(days=days),today-timedelta(days=1)
    elif preset=='Custom' and custom: a,b=custom
    else: raise ValueError('Select a valid reporting period.')
    if a>b: raise ValueError('Start date must be on or before end date.')
    tz=ZoneInfo(zone)
    start=datetime.combine(a,time.min,tz).astimezone(UTC)
    end=datetime.combine(b+timedelta(days=1),time.min,tz).astimezone(UTC)
    if start>now: raise ValueError('The reporting period cannot start in the future.')
    return start,min(end,now),a,b

def phone(value):
    try:
        p=phonenumbers.parse(str(value),'US')
        return phonenumbers.format_number(p,phonenumbers.PhoneNumberFormat.E164) if phonenumbers.is_possible_number(p) else ''
    except phonenumbers.NumberParseException: return ''

def field_code(f): return str(f.get('field_code') or f.get('key') or '')
def field_name(f): return str(f.get('field_name') or f.get('name') or field_code(f))
def field_value(person,code): return (person.get('custom_fields') or {}).get(code,person.get(code))
def plain(value):
    if isinstance(value,dict): return plain(value.get('label',value.get('value','')))
    if isinstance(value,list): return '; '.join(plain(x) for x in value)
    s=str(value if value is not None else '').strip()
    return '' if s.casefold() in ('-','n/a','none','null') else s

def auto_mapping(fields):
    result={}
    for u in UTMS:
        hits=[field_code(f) for f in fields if field_name(f).strip().casefold()==u]
        result[u]=hits[0] if len(hits)==1 else ''
    return result

def tag_entries(person,code,fields):
    f=next((f for f in fields if field_code(f)==code),{})
    if code=='label_ids' and not f.get('options'):
        f=next((f for f in fields if field_code(f)=='label' and f.get('options')),f)
    opts={str(o['id']):str(o['label']) for o in (f.get('options') or []) if 'id' in o}
    if code=='label_ids':
        vals=person.get('labels') or person.get('label_ids') or []
    else: vals=field_value(person,code)
    if vals is None: return {}
    if not isinstance(vals,list):
        vals=re.split(r'[,;\n]',str(vals)) if isinstance(vals,str) else [vals]
    result={}
    for v in vals:
        if isinstance(v,dict):
            key=str(v.get('id',v.get('value','')))
            label=plain(v.get('label',v.get('name',opts.get(key,key))))
            token='id:'+key
        elif str(v).strip() in opts or code=='label_ids' or f.get('field_type') in ('set','enum'):
            key=str(v).strip(); token='id:'+key; label=opts.get(key,key)
        else:
            label=plain(v); token='text:'+label.casefold()
        if label: result[token]=label
    return result

def tag_catalog(fields,people,code):
    f=next((f for f in fields if field_code(f)==code),{})
    if code=='label_ids' and not f.get('options'):
        f=next((f for f in fields if field_code(f)=='label' and f.get('options')),f)
    result={'id:'+str(o['id']):str(o['label']) for o in (f.get('options') or []) if 'id' in o}
    for p in people: result.update(tag_entries(p,code,fields))
    return dict(sorted(result.items(),key=lambda item:(item[1].casefold(),item[0])))

def people_rows(people,fields,options,start,end):
    mapping=options['utm_fields']; tag_field=options.get('tag_field','label_ids')
    selected=set(options.get('tags',[])); rows=[]; seen=set(); invalid_dates=0
    for p in people:
        pid=str(p['id'])
        if pid in seen: continue
        seen.add(pid)
        created=parse_dt(p.get('add_time'))
        if created is None: invalid_dates+=1; continue
        if not start<=created<end: continue
        utms={u:plain(field_value(p,mapping.get(u,''))) if mapping.get(u) else '' for u in UTMS}
        if not any(utms.values()): continue
        tags=tag_entries(p,tag_field,fields)
        matched=bool(selected) and (selected<=set(tags) if options.get('tag_rule')=='All selected tags' else bool(selected & set(tags)))
        phones=[]
        for x in p.get('phones',p.get('phone',[])) or []:
            n=phone(x.get('value') if isinstance(x,dict) else x)
            if n and n not in phones: phones.append(n)
        emails=[str(x.get('value','')) if isinstance(x,dict) else str(x) for x in (p.get('emails',p.get('email',[])) or [])]
        row={'person_id':pid,'name':p.get('name',''),'created_at':created.isoformat(),
             'phones':phones,'emails':emails,**utms,'tags':list(tags.values()),'tag_tokens':list(tags),
             'matches_tag':matched,'matched_tags':[tags[k] for k in tags if k in selected],
             'call_count':None,'answered_calls':None,'transcripts_available':None,'aircall_status':'not requested'}
        group_fields=options.get('group_by',['utm_source','utm_campaign'])
        row['group_key']=json.dumps([utms[u] for u in group_fields],ensure_ascii=False,separators=(',',':'))
        row['utm_group']=' / '.join(f'{u}={utms[u] or "(missing)"}' for u in group_fields)
        rows.append(row)
    return rows,invalid_dates

def statistic_tags(catalog, configured=None):
    """Exact tag IDs; each statistic accepts any of its configured tags."""
    return {name:list(configured[name]) if configured is not None and name in configured else
            [token for token,label in catalog.items() if label.strip().casefold()==name]
            for name in ('qualified','contacted')}

def group_tabs(summary):
    """Use actual UTM values, disambiguating delimiter/missing-value collisions."""
    labels=[' / '.join(value or '(missing)' for value in json.loads(r['group_key'])) for r in summary]
    return {label if labels.count(label)==1 else f'{label} [{hashlib.sha256(r["group_key"].encode()).hexdigest()[:10]}]':r['group_key']
            for label,r in zip(labels,summary)}

def group_filename(group_key):
    values=json.loads(group_key)
    slug=re.sub(r'[^A-Za-z0-9_-]+','_', '_'.join(values)).strip('_')[:100] or 'utm'
    return slug+'_'+hashlib.sha256(group_key.encode()).hexdigest()[:10]

def summarize(rows,catalog=None,stat_tags=None):
    stats=statistic_tags(catalog or {},stat_tags)
    groups=defaultdict(list)
    for row in rows: groups[row['group_key']].append(row)
    out=[]
    for key,group in sorted(groups.items(),key=lambda x:x[1][0]['utm_group']):
        matched=sum(r['matches_tag'] for r in group)
        result={'group_key':key,'utm_group':group[0]['utm_group'],'people':len(group),'matching_people':matched,
                'match_pct':round(100*matched/len(group),2),'aircall_people_fetched':sum(r['aircall_status']=='complete' for r in group),
                'calls':sum(r['call_count'] or 0 for r in group)}
        for name,tokens in stats.items():
            count=sum(bool(set(tokens)&set(r['tag_tokens'])) for r in group) if tokens else None
            result[name+'_people']=count
            result[name+'_pct']=round(100*count/len(group),2) if count is not None else None
        for token,label in (catalog or {}).items(): result[f'tag: {label} [{token}]']=sum(token in r['tag_tokens'] for r in group)
        out.append(result)
    return out

def summarize_all(rows,catalog=None,stat_tags=None):
    """Overall rates use the people denominator, never averages of group rates."""
    combined=[{**r,'group_key':'[]','utm_group':'All UTM codes'} for r in rows]
    totals=summarize(combined,catalog,stat_tags)
    return totals[0] if totals else None

def public_rows(rows):
    return [{k:('; '.join(v) if isinstance(v,list) else v) for k,v in r.items() if k not in ('tag_tokens','group_key')} for r in rows]

def csv_bytes(rows,columns=None):
    fields=columns or list(dict.fromkeys(k for r in rows for k in r))
    out=io.StringIO(newline=''); writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore'); writer.writeheader()
    def safe(v):
        if isinstance(v,(dict,list)): v=json.dumps(v,ensure_ascii=False)
        if isinstance(v,str) and (v.lstrip().startswith(('=','+','-','@')) or v.startswith(('\r','\n','\t'))): v="'"+v
        return v
    writer.writerows({k:safe(v) for k,v in r.items()} for r in rows)
    return out.getvalue().encode('utf-8-sig')

def six_month_boundary(now):
    year,month0=divmod(now.year*12+now.month-1-6,12); month=month0+1
    return now.replace(year=year,month=month,day=min(now.day,calendar.monthrange(year,month)[1]))

