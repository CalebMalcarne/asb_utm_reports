from pathlib import Path
import io
import zipfile
import streamlit as st
from streamlit.testing.v1 import AppTest


def app_path():
    return str(Path(__file__).resolve().parents[1]/'app.py')


def test_standalone_demo_needs_no_pipedrive_and_exports_highlights(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    downloads={}
    original=st.download_button
    def download(label,data,*args,**kwargs):
        downloads[kwargs['key']]=data
        return original(label,data,*args,**kwargs)
    monkeypatch.setattr(st,'download_button',download)
    app=AppTest.from_file(app_path(),default_timeout=30).run()
    next(r for r in app.radio if r.label=='Menu').set_value('Aircall export & search').run()
    assert not app.exception
    next(c for c in app.checkbox if c.label=='Use synthetic Aircall demo').check().run()
    next(n for n in app.number_input if n.label=='Transcript requests per batch').set_value(2).run()
    next(b for b in app.button if b.label=='Fetch Aircall calls').click().run()
    assert not app.exception
    assert len(app.session_state.ac_dataset['calls'])==5
    assert sum(c['transcript_status']=='not requested' for c in app.session_state.ac_dataset['calls'])==3
    next(b for b in app.button if b.label=='Fetch next transcript batch').click().run()
    assert not app.exception
    next(t for t in app.text_area if t.label=='Keywords or phrases (one per line)').set_value('retirement protection').run()
    assert not app.exception
    with zipfile.ZipFile(io.BytesIO(downloads['ac_zip'])) as z:
        assert 'highlighted_transcripts/Demo_Contact_1_901.html' in z.namelist()
        assert 'highlighted_transcripts/Demo_Contact_3_903.html' in z.namelist()
        assert not any('902.html' in name for name in z.namelist())
    next(r for r in app.radio if r.label=='Menu').set_value('Reports').run()
    assert not app.exception
    assert any('Set up Pipedrive' in str(x.value) for x in app.info)


def test_main_report_search_opt_in_preserves_existing_aircall_ui(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    downloads={}
    original=st.download_button
    def download(label,data,*args,**kwargs):
        downloads[kwargs['key']]=data
        return original(label,data,*args,**kwargs)
    monkeypatch.setattr(st,'download_button',download)
    app=AppTest.from_file(app_path(),default_timeout=30).run()
    next(b for b in app.button if b.label=='Load demo').click().run()
    next(b for b in app.button if b.label=='Fetch calls & transcripts').click().run()
    next(c for c in app.checkbox if c.label=='Enable transcript keyword search').check().run()
    next(t for t in app.text_area if t.label=='Keywords or phrases (one per line)').set_value('synthetic example').run()
    assert not app.exception
    assert len(app.session_state.report['calls'])==3
    with zipfile.ZipFile(io.BytesIO(downloads['zip_all'])) as z:
        assert 'all_People.csv' in z.namelist()
        assert 'Overall Summary.pdf' in z.namelist()
        assert 'Transcript_matches.csv' in z.namelist()
        assert len([n for n in z.namelist() if n.startswith('highlighted_transcripts/')])==3
    next(c for c in app.checkbox if c.label=='Include phrase results and highlighted transcripts in Full report ZIP').uncheck().run()
    assert not app.exception
    with zipfile.ZipFile(io.BytesIO(downloads['zip_all'])) as z:
        assert 'Transcript_matches.csv' not in z.namelist()
