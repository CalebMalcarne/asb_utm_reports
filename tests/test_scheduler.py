from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
import requests
from storage import Store
from scheduler import next_run,execute_job
from mailer import make_payload,send,DeliveryUnknown

UTC=timezone.utc

def spec():
    return {'frequency':'Weekly','timezone':'America/New_York','at':'09:00','weekday':0,'monthday':31,'hours':168,'anchor':0,
            'period':'Last week','days':7,'recipients':'manager@example.invalid',
            'options':{'utm_fields':{'utm_source':'x'},'tag_field':'label_ids','tags':['id:1'],'group_by':['utm_source']},
            'groups':None,'include_details':False,'include_calls':False,'include_transcripts':False}

def settings():
    return {'company':'example','pipedrive_token':'pd-secret','aircall_id':'id','aircall_token':'ac-secret','sendgrid_key':'sg-secret','sender_email':'reports@example.invalid'}

def test_weekly_timezone_month_end_and_dst():
    s=spec()
    after=datetime(2026,10,30,12,tzinfo=UTC).timestamp()
    assert datetime.fromtimestamp(next_run(s,after),UTC).isoformat()=='2026-11-02T14:00:00+00:00'
    s.update(frequency='Monthly')
    after=datetime(2026,2,1,tzinfo=UTC).timestamp()
    assert datetime.fromtimestamp(next_run(s,after),UTC).date().isoformat()=='2026-02-28'
    s.update(frequency='Daily',at='02:30')
    after=datetime(2026,3,8,5,tzinfo=UTC).timestamp()
    assert datetime.fromtimestamp(next_run(s,after),UTC).isoformat()=='2026-03-08T07:30:00+00:00'

def test_credentials_not_plaintext_and_one_atomic_claim(tmp_path):
    store=Store(tmp_path);store.save_settings(settings())
    assert store.settings()['sendgrid_key']=='sg-secret'
    assert b'sg-secret' not in store.db.read_bytes()
    s=spec();store.save_schedule('test',s,100,True)
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:store.claim(200,next_run),range(2)))
    assert sum(x is not None for x in results)==1
    assert len(store.jobs())==1

def test_sendgrid_recipient_isolation_and_accepted():
    p=make_payload(settings(),'one@example.invalid,two@example.invalid','Test','Body',{'summary.csv':b'a,b\n1,2'})
    assert len(p['personalizations'])==2
    assert all(len(x['to'])==1 for x in p['personalizations'])
    assert 'sg-secret' not in str(p)
    class Response: status_code=202;headers={'X-Message-Id':'123'}
    assert send(settings(),p,post=lambda *a,**k:Response())=='123'

def test_send_timeout_no_automatic_retry():
    called=[]
    def post(*a,**k): called.append(1);raise requests.Timeout()
    with pytest.raises(DeliveryUnknown): send(settings(),{},post=post)
    assert called==[1]

def test_schedule_fetches_fresh_report_and_sends_once(tmp_path,monkeypatch):
    import scheduler
    from demo import sample
    store=Store(tmp_path);store.save_settings(settings())
    data=sample();s=spec();s['options']=data['options']
    store.save_schedule('test',s,100,True)
    job=store.claim(200,next_run)
    events=[]
    monkeypatch.setattr(scheduler,'fetch_people',lambda *a:(events.append('fetch') or data))
    monkeypatch.setattr(scheduler,'send',lambda *a:(events.append('send') or 'msg'))
    scheduler.execute_job(store,job)
    assert events==['fetch','send']
    assert store.jobs()[0]['status']=='accepted'
    assert store.claim(200,next_run) is None

def test_pausing_during_preparation_cancels_send(tmp_path,monkeypatch):
    import scheduler
    from demo import sample
    store=Store(tmp_path);store.save_settings(settings())
    data=sample();s=spec();s['options']=data['options']
    sid=store.save_schedule('test',s,100,True)
    job=store.claim(200,next_run)
    def fetch(*a): store.enable(sid,False);return data
    monkeypatch.setattr(scheduler,'fetch_people',fetch)
    monkeypatch.setattr(scheduler,'send',lambda *a:pytest.fail('should not send'))
    execute_job(store,job)
    assert store.jobs()[0]['status']=='cancelled'
