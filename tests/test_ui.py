from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_demo_report_aircall_settings_and_email_screens(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    assert not app.exception
    next(b for b in app.button if b.label=='Load demo').click().run()
    assert not app.exception
    assert [m.value for m in app.metric]==['5','3','3','60.0%']
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
