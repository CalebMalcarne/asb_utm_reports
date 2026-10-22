from datetime import datetime,timedelta,timezone
from pathlib import Path
from threading import Event

from streamlit.testing.v1 import AppTest

from api import APIError
from core import parse_dt
from demo import sample,DemoAircall
from service import build,select_rows
from speed_lookup import SpeedLookup,lookup_key


def test_lookup_checks_every_number_and_first_outbound_without_transcripts():
    data=sample(); report=build(data,data['options'])
    row=report['rows'][0]; row['phones'].append('+12025550999')
    class Client:
        def __init__(self): self.requests=[]
        def calls(self,number,start,end):
            self.requests.append((number,start,end))
            return [dict(id='later',raw_digits=number,direction='outbound',started_at=start+601),
                    dict(id='inbound',raw_digits=number,direction='inbound',started_at=start+10),
                    dict(id='prior',raw_digits=number,direction='outbound',started_at=start-10),
                    dict(id='other',raw_digits='+12025550888',direction='outbound',started_at=start+20),
                    dict(id='first',raw_digits=number,direction='outbound',started_at=start+(300 if number.endswith('999') else 400))]
        def transcript(self,*args): raise AssertionError('Automatic lookup must not fetch transcripts')
    client=Client(); job=SpeedLookup(report,{},client)
    job._run(); results,done=job.snapshot()
    assert done and len(results)==len(report['rows'])
    assert results[row['person_id']]['speed_to_lead_seconds']==300
    assert results[row['person_id']]['speed_to_lead_color']=='Green'
    assert all(end==job.end.timestamp() for _,_,end in client.requests)
    assert client.requests[0][1]==parse_dt(row['created_at']).timestamp()
    assert not report['calls'] # speed lookup never replaces manual activity/transcripts
    report['speed_results']=results
    assert select_rows(report)[0]['first_call_id']=='first'


def test_unavailable_history_shared_numbers_and_api_errors_are_not_zero():
    data=sample(); report=build(data,data['options'])
    report['rows'][1]['phones']=report['rows'][0]['phones'][:]
    report['rows'][2]['phones']=[]
    report['rows'][3]['created_at']=(datetime.now(timezone.utc)-timedelta(days=365)).isoformat()
    class Client:
        def calls(self,*args): raise APIError('secret-bearing error must not be exposed',401)
    job=SpeedLookup(report,{},Client()); job._run(); results,done=job.snapshot()
    assert done
    assert 'shared' in results['1']['speed_to_lead']
    assert 'no valid phone' in results['3']['speed_to_lead']
    assert 'outside Aircall history' in results['4']['speed_to_lead']
    assert results['5']['speed_to_lead']=='Aircall lookup failed (HTTP 401)'
    assert all(r['speed_to_lead_seconds'] is None and r['speed_to_lead_color']=='' for r in results.values())


def test_ui_renders_loading_then_updates_all_tabs_without_transcripts(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    entered=Event(); release=Event(); original=DemoAircall.calls
    def blocked(self,*args):
        entered.set()
        assert release.wait(10)
        return original(self,*args)
    def no_transcript(*args): raise AssertionError('Unexpected transcript request')
    monkeypatch.setattr(DemoAircall,'calls',blocked)
    monkeypatch.setattr(DemoAircall,'transcript',no_transcript)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=5).run()
    try:
        next(b for b in app.button if b.label=='Load demo').click().run()
        assert not app.exception and entered.wait(2)
        job=app.session_state.speed_lookup
        assert set(app.tabs[0].dataframe[1].value['speed_to_lead'])=={'Loading…'}
        for tab in app.tabs:
            assert tab.checkbox[0].label=='Only Show Matches'
        app.tabs[0].checkbox[0].check().run()
        assert len(app.tabs[0].dataframe[1].value)==3
        assert all(not tab.checkbox[0].value for tab in app.tabs[1:])
        release.set(); job.thread.join(timeout=5)
        app.run()
        assert not app.exception and job.snapshot()[1]
        assert app.session_state.speed_lookup is job # no repeat fetch on reruns
        assert 'Loading…' not in set(app.tabs[0].dataframe[1].value['speed_to_lead'])
        assert len(app.session_state.report['speed_results'])==5 # unmatched people also checked
        assert app.session_state.report['calls']==[]
        assert app.tabs[0].checkbox[0].value
        assert not app.get('tab_container')[0].proto.tab_container.id
    finally:
        release.set()


def test_lookup_identity_changes_for_new_data_or_credentials_but_not_tags():
    data=sample(); report=build(data,data['options'])
    key=lookup_key(report,{'aircall_token':'one'})
    report['options']['tags']=[]
    assert lookup_key(report,{'aircall_token':'one'})==key
    assert lookup_key(report,{'aircall_token':'two'})!=key
    report['meta']['fetched_at']='new report'
    assert lookup_key(report,{'aircall_token':'one'})!=key
