import csv
import io
import zipfile
from pathlib import Path
import streamlit as st
from streamlit.testing.v1 import AppTest
from agent_analytics import safe_agent_folder


def test_agent_demo_expand_sort_exports_and_scoped_transcripts(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    downloads={}; original=st.download_button
    def download(label,data,*args,**kwargs):
        assert kwargs.get('on_click')=='ignore'
        downloads[kwargs['key']]=data
        return original(label,data,*args,**kwargs)
    monkeypatch.setattr(st,'download_button',download)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    next(r for r in app.radio if r.label=='Menu').set_value('Agent Analytics').run()
    next(c for c in app.checkbox if c.label=='Use synthetic agent demo').check().run()
    next(b for b in app.button if b.label=='Fetch agent calls').click().run()
    assert not app.exception
    assert len(app.session_state.agents_dataset['calls'])==5
    assert all(c['transcript_status']=='not requested' for c in app.session_state.agents_dataset['calls'])
    assert len(app.dataframe)==1 # only rankings while collapsed
    with zipfile.ZipFile(io.BytesIO(downloads['agents_all_report'])) as z:
        assert len(list(csv.DictReader(io.StringIO(z.read('Agent_rankings.csv').decode('utf-8-sig')))))==3
        assert z.read('Agent_Report.pdf').startswith(b'%PDF')
    prefix=safe_agent_folder('id:101')
    app.button(key=prefix+'_toggle').click().run()
    assert not app.exception and len(app.dataframe)==2
    assert prefix+'_report' in downloads
    app.button(key=prefix+'_fetch_text').click().run()
    assert not app.exception
    calls=app.session_state.agents_dataset['calls']
    assert all(c['transcript_status']=='available' for c in calls if c['agent_id']=='101')
    assert all(c['transcript_status']=='not requested' for c in calls if c['agent_id']!='101')
    with zipfile.ZipFile(io.BytesIO(downloads[prefix+'_transcripts'])) as z:
        assert len([n for n in z.namelist() if n.endswith('.txt')])==2
        assert not any('_903.txt' in n for n in z.namelist())
    next(s for s in app.selectbox if s.label=='Rank / sort agents by').set_value('Workday response time').run()
    assert not app.exception and app.session_state[prefix+'_expanded']
    # Connection/mode changes must discard the previous dataset.
    next(c for c in app.checkbox if c.label=='Use synthetic agent demo').uncheck().run()
    assert not app.exception and 'agents_dataset' not in app.session_state


def test_transcript_failure_keeps_completed_calls_and_resumes(tmp_path,monkeypatch):
    from agent_demo import DemoAgents
    from api import APIError
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    original=DemoAgents.transcript
    def fail(self,cid):
        if cid=='902': raise APIError('quota exceeded',429)
        return original(self,cid)
    monkeypatch.setattr(DemoAgents,'transcript',fail)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    next(r for r in app.radio if r.label=='Menu').set_value('Agent Analytics').run()
    next(c for c in app.checkbox if c.label=='Use synthetic agent demo').check().run()
    next(b for b in app.button if b.label=='Fetch agent calls').click().run()
    app.button(key='agents_all_fetch_text').click().run()
    assert not app.exception
    assert app.session_state.agents_dataset['calls'][0]['transcript_status']=='available'
    assert app.session_state.agents_dataset['calls'][1]['transcript_status']=='not requested'
    requested=[]
    def resume(self,cid): requested.append(cid); return original(self,cid)
    monkeypatch.setattr(DemoAgents,'transcript',resume)
    app.button(key='agents_all_fetch_text').click().run()
    assert not app.exception and '901' not in requested
    assert not any(c['transcript_status']=='not requested' for c in app.session_state.agents_dataset['calls'])


def test_response_lookup_replaces_stale_credentials_and_refreshes_rankings(tmp_path,monkeypatch):
    import hashlib
    from test_agent_analytics import fixture
    from speed_lookup import SpeedLookup
    from storage import Store
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(SpeedLookup,'start',lambda self:None)
    settings=Store().settings(); settings.update(aircall_id='new-id',aircall_token='new-token')
    Store().save_settings(settings)
    dataset,report=fixture(); dataset['meta']['demo']=False; report['meta']['demo']=False
    old=SpeedLookup(report,{'aircall_id':'old-id','aircall_token':'old-token'})
    old.results=report['speed_results']; old.done=True
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30)
    app.session_state['report']=report
    app.session_state['agents_dataset']=dataset
    app.session_state['agents_identity']=hashlib.sha256(b'new-id\0new-token').hexdigest()
    app.session_state['speed_lookup']=old
    app.session_state['speed_lookup_key']='old connection'
    app.run()
    next(r for r in app.radio if r.label=='Menu').set_value('Agent Analytics').run()
    assert not app.exception and old.cancelled.is_set()
    fresh=app.session_state.speed_lookup
    assert fresh is not old
    assert set(app.dataframe[0].value['average_response'])=={'—'}
    fresh.results=report['speed_results']; fresh.done=True
    app.run()
    assert not app.exception
    rows=app.dataframe[0].value
    assert rows.loc[rows['agent_id']=='A','workday_average'].iloc[0]=='0h 05m 00s'
    app.button(key='agents_refresh_responses').click().run()
    assert not app.exception and app.session_state.speed_lookup is not fresh
