from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_demo_report_aircall_settings_and_email_screens(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    assert not app.exception
    next(b for b in app.button if b.label=='Load demo').click().run()
    assert not app.exception
    assert [m.value for m in app.tabs[0].metric]==['5','3','60.0%','20.0%']
    next(b for b in app.button if b.label=='Fetch calls & transcripts').click().run()
    assert not app.exception
    assert len(app.session_state.report['calls'])==3
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

def test_all_and_individual_tabs_have_scoped_controls_and_no_switch_reruns(tmp_path,monkeypatch):
    import streamlit as st
    import csv,io
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    downloads={}; original=st.download_button
    def download(label,data,*args,**kwargs):
        assert kwargs.get('on_click')=='ignore'
        downloads[kwargs['key']]=(data,args[0] if args else kwargs.get('file_name'))
        return original(label,data,*args,**kwargs)
    monkeypatch.setattr(st,'download_button',download)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    next(b for b in app.button if b.label=='Load demo').click().run()
    assert not app.exception
    assert [t.label for t in app.tabs]==['All UTM codes','AFLT2421 / Karmaoholic - 48824','instagram / organic','meta / retirement_october']
    # Static tabs do not register a widget that emits tab-change reruns.
    assert not app.get('tab_container')[0].proto.tab_container.id
    assert [m.value for m in app.tabs[0].metric]==['5','3','60.0%','20.0%']
    assert [m.value for m in app.tabs[3].metric]==['2','1','50.0%','50.0%']
    def rows(key): return list(csv.DictReader(io.StringIO(downloads[key][0].decode('utf-8-sig'))))
    assert {r['person_id'] for r in rows('people_all')}=={'1','2','3','4','5'}
    assert len(rows('summary_all'))==4
    assert rows('summary_all')[0]['row_type']=='total'
    assert rows('summary_all')[0]['qualified_pct']=='60.0'
    key=next(r['group_key'] for r in app.session_state.report['rows'] if r['utm_source']=='meta')
    scope='group_'+key
    assert {r['person_id'] for r in rows('people_'+scope)}=={'3','4'}
    assert downloads['people_all'][1]!=downloads['people_'+scope][1]
    assert set(app.tabs[3].dataframe[0].value['person_id'])=={'3','4'}
    app.tabs[3].checkbox[0].check().run()
    assert not app.exception
    assert {r['person_id'] for r in rows('people_'+scope)}=={'4'}
    assert len(rows('people_all'))==5 # independent detail filters
    assert rows('summary_'+scope)[0]['qualified_pct']=='50.0'
    app.tabs[3].button[0].click().run()
    assert not app.exception
    assert {c['person_id'] for c in app.session_state.report['calls']}=={'4'}
    assert rows('activity_csv_'+scope)[0]['person_id']=='4'
    assert not app.get('tab_container')[0].proto.tab_container.id

def test_save_and_check_sendgrid_uses_new_form_credentials_without_email(tmp_path,monkeypatch):
    from storage import Store
    import mailer
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    store=Store(); s=store.settings();s.update(sendgrid_key='old-key',sender_email='old@example.invalid')
    store.save_settings(s)
    calls=[]
    class Response: status_code=200
    def post(url,**kwargs):
        calls.append((url,kwargs))
        assert store.settings()['sendgrid_key']=='SG.new_id.new_secret'
        return Response()
    monkeypatch.setattr(mailer.requests,'post',post)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    next(r for r in app.radio if r.label=='Menu').set_value('Settings').run()
    next(x for x in app.text_input if x.label=='SendGrid API key').set_value(' Bearer SG.new_id.new_secret ')
    next(x for x in app.text_input if x.label=='Verified sender email').set_value('new@example.invalid')
    next(b for b in app.button if b.label=='Save & check SendGrid (no email)').click().run()
    assert not app.exception and not app.error
    assert len(calls)==1
    assert calls[0][1]['headers']['Authorization']=='Bearer SG.new_id.new_secret'
    assert calls[0][1]['json']['from']['email']=='new@example.invalid'
    assert calls[0][1]['json']['mail_settings']['sandbox_mode']['enable'] is True
    assert store.jobs()==[] # validation is not recorded as a delivery
    assert any('Sandbox validation passed' in x.value for x in app.success)
