"""Persistent local scheduler. Run independently or via launch.py."""
import argparse
import math
import threading
import time
from datetime import datetime,timedelta,time as walltime,timezone
from zoneinfo import ZoneInfo
from storage import Store
from core import period,parse_dt
from service import fetch_people,build,enrich,select_rows
from mailer import recipients,report_payload,send,DeliveryUnknown

UTC=timezone.utc

def next_run(spec,after):
    kind=spec['frequency']; tz=ZoneInfo(spec['timezone'])
    if kind=='Every N hours':
        step=int(spec['hours'])*3600
        if step<3600: raise ValueError('Interval must be at least one hour.')
        anchor=float(spec['anchor'])
        return anchor+(math.floor(max(0,after-anchor)/step)+1)*step if after>=anchor else anchor
    local=datetime.fromtimestamp(after,tz)
    hour,minute=map(int,spec['at'].split(':'))
    if not (0<=hour<24 and 0<=minute<60): raise ValueError('Invalid send time.')
    for i in range(370):
        day=local.date()+timedelta(days=i)
        if kind=='Weekly' and day.weekday()!=int(spec['weekday']): continue
        if kind=='Monthly':
            import calendar
            if day.day!=min(int(spec['monthday']),calendar.monthrange(day.year,day.month)[1]): continue
        if kind not in ('Daily','Weekly','Monthly'): raise ValueError('Invalid schedule frequency.')
        # fold=0 chooses the first fall-back occurrence; nonexistent spring time moves forward.
        candidate=datetime.combine(day,walltime(hour,minute),tz).replace(fold=0).astimezone(UTC).timestamp()
        if candidate>after: return candidate
    raise ValueError('Could not calculate the next schedule occurrence.')

def validate_schedule(spec,settings):
    recipients(spec['recipients'])
    recipients(settings.get('sender_email',''))
    if not settings.get('sendgrid_key') or not settings.get('pipedrive_token') or not settings.get('company'):
        raise ValueError('Save Pipedrive and SendGrid settings before enabling a schedule.')
    if not any(spec['options']['utm_fields'].values()): raise ValueError('Map UTM fields before scheduling.')
    if not spec['options'].get('tags'): raise ValueError('Select at least one report tag before scheduling.')
    if spec.get('groups')==[]: raise ValueError('Choose UTM groups or All groups.')
    if spec.get('include_calls') and (not settings.get('aircall_id') or not settings.get('aircall_token')):
        raise ValueError('Save Aircall credentials for scheduled activity reports.')
    if spec['period'] not in ('Yesterday','Last week','Last month','Previous N complete days'):
        raise ValueError('Choose a complete relative reporting period.')
    if not 1<=int(spec.get('days',7))<=365: raise ValueError('Lookback must be 1–365 days.')
    next_run(spec,time.time())

def execute_job(store,job,now=None):
    jid=job['job_id']; spec=job['spec']; now=now or datetime.now(UTC)
    try:
        settings=store.settings(); validate_schedule(spec,settings)
        start,end,_,_=period(spec['period'],spec['timezone'],now,days=int(spec.get('days',7)))
        data=fetch_people(settings,spec['options'],start,end)
        report=build(data,spec['options'])
        if spec.get('include_calls'):
            ids=[r['person_id'] for r in select_rows(report,spec.get('groups'),True)]
            activity_end=now if spec.get('followup')=='Through send time' else end
            report=enrich(report,settings,ids,start,activity_end,spec.get('include_transcripts',False))
        payload=report_payload(settings,spec['recipients'],report,spec.get('groups'),spec.get('matched_only',False),
                               spec.get('include_details',False),spec.get('include_calls',False),spec.get('include_transcripts',False))
        # Pausing/editing a schedule while it prepares prevents its pending email.
        current=next((s for s in store.schedules() if s['id']==job['schedule_id']),None)
        if not current or not current['enabled'] or current['spec']!=spec:
            store.finish(jid,'cancelled','Schedule changed or was paused before send.'); return
        store.finish(jid,'sending','One SendGrid request in progress; do not resend while unresolved.')
        message_id=send(settings,payload)
        store.finish(jid,'accepted',f'SendGrid accepted message {message_id}. Acceptance is not confirmed delivery.')
    except DeliveryUnknown as exc: store.finish(jid,'uncertain',str(exc))
    except Exception as exc:
        # Only known safe messages; generic unknown exceptions can contain request data.
        from api import APIError
        message=str(exc) if isinstance(exc,(ValueError,APIError)) else f'{type(exc).__name__}; check local configuration.'
        store.finish(jid,'failed',message)

def tick(store,now=None):
    now=now or time.time()
    job=store.claim(now,next_run)
    if job: execute_job(store,job,datetime.fromtimestamp(now,UTC))
    return job

def run_loop(store,stop=None):
    stop=stop or threading.Event()
    def beat():
        while not stop.is_set():
            store.put('worker_heartbeat',time.time()); stop.wait(10)
    heartbeat=threading.Thread(target=beat,daemon=True); heartbeat.start()
    try:
        while not stop.is_set():
            try: tick(store)
            except Exception as exc: print(f'Scheduler tick failed: {type(exc).__name__}',flush=True)
            stop.wait(15)
    finally: stop.set(); heartbeat.join(timeout=1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--once',action='store_true'); args=parser.parse_args()
    store=Store()
    if args.once: tick(store)
    else: run_loop(store)
