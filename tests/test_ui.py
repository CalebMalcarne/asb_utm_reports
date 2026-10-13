from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_demo_report_aircall_settings_and_email_screens(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    assert not app.exception
    next(b for b in app.button if b.label=='Load demo').click().run()
    assert not app.exception
    assert [m.value for m in app.metric]==['2','2','100.0%','0.0%']
    next(b for b in app.button if b.label=='Fetch calls & transcripts').click().run()
    assert not app.exception
    assert len(app.session_state.report['calls'])==2
    next(r for r in app.radio if r.label=='Menu').set_value('Settings').run()
    assert not app.exception
    assert any(x.label=='Pipedrive API token' for x in app.text_input)
    next(r for r in app.radio if r.label=='Menu').set_value('Email & scheduling').run()
    assert not app.exception
    assert any(x.label=='Test email address' for x in app.text_input)
    assert next(b for b in app.button if b.label=='Send current report now').disabled

def test_live_schedule_save_and_test_email_use_mocks(tmp_path,monkeypatch):
    from demo import sample
    from storage import Store
    import mailer
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    store=Store()
    s=store.settings();s.update(company='example',pipedrive_token='fake',sendgrid_key='fake',sender_email='reports@example.invalid')
    store.save_settings(s)
    data=sample();data['demo']=False
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30)
    app.session_state['data']=data
    app.run()
    assert not app.exception
    next(r for r in app.radio if r.label=='Menu').set_value('Email & scheduling').run()
    assert not app.exception
    next(x for x in app.text_area if x.label=='Scheduled recipient emails').set_value('manager@example.invalid')
    next(b for b in app.button if b.label=='Save schedule').click().run()
    assert not app.exception
    assert len(store.schedules())==1
    assert not store.schedules()[0]['enabled']
    class Response: status_code=202;headers={'X-Message-Id':'test-accepted'}
    calls=[]
    def post(*args,**kwargs): calls.append(kwargs['json']);return Response()
    monkeypatch.setattr(mailer.requests,'post',post)
    next(x for x in app.text_input if x.label=='Test email address').set_value('test@example.invalid')
    next(b for b in app.button if b.label=='Send test email').click().run()
    assert not app.exception
    assert len(calls)==1
    assert 'attachments' not in calls[0]
    assert store.jobs()[0]['status']=='accepted'

def test_connect_saves_current_form_values_before_authentication(tmp_path,monkeypatch):
    from storage import Store
    from api import Pipedrive
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    store=Store(); settings=store.settings()
    settings.update(company='oldcompany',pipedrive_token='old-token')
    store.save_settings(settings)
    calls=[]
    def current_user(self):
        calls.append(('account',self.company,self.session.headers['x-api-token']))
        assert store.settings()['pipedrive_token']=='new-token'
        return {'id':7}
    def fields(self):
        calls.append(('fields',self.company,self.session.headers['x-api-token']))
        return []
    monkeypatch.setattr(Pipedrive,'current_user',current_user)
    monkeypatch.setattr(Pipedrive,'fields',fields)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    next(r for r in app.radio if r.label=='Menu').set_value('Settings').run()
    next(x for x in app.text_input if x.label=='Company subdomain').set_value(' https://newcompany.pipedrive.com/ ')
    next(x for x in app.text_input if x.label=='Pipedrive API token').set_value(' new-token ')
    next(b for b in app.button if b.label=='Save & connect Pipedrive / refresh fields').click().run()
    assert not app.exception
    assert not app.error
    assert calls==[('account','newcompany','new-token'),('fields','newcompany','new-token')]
    assert store.settings()['company']=='newcompany'

def test_switching_utm_tabs_changes_stats_people_and_download_contents(tmp_path,monkeypatch):
    import streamlit as st
    import csv,io,json
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    downloads={}; original=st.download_button
    def download(label,data,*args,**kwargs):
        downloads[label]=(data,args[0] if args else kwargs.get('file_name'))
        return original(label,data,*args,**kwargs)
    monkeypatch.setattr(st,'download_button',download)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    next(b for b in app.button if b.label=='Load demo').click().run()
    assert not app.exception
    assert [t.label for t in app.tabs]==['AFLT2421 / Karmaoholic - 48824','instagram / organic','meta / retirement_october']
    def rows(label): return list(csv.DictReader(io.StringIO(downloads[label][0].decode('utf-8-sig'))))
    assert {r['person_id'] for r in rows('People CSV')}=={'1','2'}
    before_filename=downloads['People CSV'][1]
    def browser_run(label):
        # AppTest's Tab is a Block, not a Widget, so supply the active tab
        # state that a real browser sends alongside checkbox/button events.
        states=app._tree.get_widget_states()
        container=app.get('tab_container')[0]
        states.widgets.add(id=container.proto.tab_container.id,string_value=label)
        app._run(states)
    browser_run('meta / retirement_october')
    assert not app.exception
    assert [m.value for m in app.metric]==['2','1','50.0%','50.0%']
    assert {r['person_id'] for r in rows('People CSV')}=={'3','4'}
    assert downloads['People CSV'][1]!=before_filename
    assert set(app.dataframe[0].value['person_id'])=={'3','4'}
    next(c for c in app.checkbox if c.label=='Show/export only people matching the selected tags').check()
    browser_run('meta / retirement_october')
    assert not app.exception
    assert {r['person_id'] for r in rows('People CSV')}=={'4'}
    assert rows('Summary CSV')[0]['qualified_pct']==rows('Summary CSV')[0]['contacted_pct']=='50.0'
    # Fetching calls stays in this tab and does not include the affiliate group.
    next(b for b in app.button if b.label=='Fetch calls & transcripts').click()
    browser_run('meta / retirement_october')
    assert not app.exception
    assert {c['person_id'] for c in app.session_state.report['calls']}=={'4'}
