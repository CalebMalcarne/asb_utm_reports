import csv
import io
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from business_hours import after_hours_details,validate_hours
from demo import sample
from service import build,select_rows,export_parts
from storage import Store


@pytest.mark.parametrize('created,flag,opening',[
    ('2026-10-22T08:59:59-04:00','Yes','2026-10-22T13:00:00+00:00'),
    ('2026-10-22T09:00:00-04:00','No',None),
    ('2026-10-22T16:59:59-04:00','No',None),
    ('2026-10-22T17:00:00-04:00','Yes','2026-10-23T13:00:00+00:00'),
    ('2026-10-23T18:00:00-04:00','Yes','2026-10-26T13:00:00+00:00'),
    ('2026-10-24T10:00:00-04:00','Yes','2026-10-26T13:00:00+00:00'),
])
def test_arrival_boundaries_and_next_workday(created,flag,opening):
    result=after_hours_details({'created_at':created},{'speed_to_lead_status':'Loading…'})
    assert result['after_hours_lead']==flag
    assert result['next_workday_start']==opening
    assert result['after_hours_response']==('Loading…' if flag=='Yes' else 'Not applicable')
    assert result['after_hours_response_seconds'] is None


@pytest.mark.parametrize('called,seconds,color,text',[
    ('08:30:00',0,'Green','Called before opening'),
    ('09:00:00',0,'Green','0h 00m 00s'),
    ('09:05:00',300,'Green','0h 05m 00s'),
    ('09:05:01',301,'Yellow','0h 05m 01s'),
    ('09:10:00',600,'Yellow','0h 10m 00s'),
    ('09:10:01',601,'Red','0h 10m 01s'),
])
def test_after_hours_response_and_colors(called,seconds,color,text):
    result=after_hours_details({'created_at':'2026-10-21T18:00:00-04:00'},
                              {'first_called_at':'2026-10-22T'+called+'-04:00'})
    assert result['after_hours_response_seconds']==seconds
    assert result['after_hours_response_color']==color
    assert result['after_hours_response']==text


def test_dst_weekend_and_custom_workdays():
    result=after_hours_details({'created_at':'2026-10-30T18:00:00-04:00'},
                              {'first_called_at':'2026-11-02T09:05:00-05:00'})
    assert result['next_workday_start']=='2026-11-02T14:00:00+00:00'
    assert result['after_hours_response_seconds']==300
    result=after_hours_details({'created_at':'2026-10-24T08:00:00-07:00'},
                              {'first_called_at':'2026-10-24T10:07:00-07:00'},
                              {'days':[5],'open':'10:00','close':'18:00','timezone':'America/Los_Angeles'})
    assert result['next_workday_start']=='2026-10-24T17:00:00+00:00'
    assert result['after_hours_response_seconds']==420


def test_unavailable_call_and_invalid_schedule():
    result=after_hours_details({'created_at':'2026-10-22T18:00:00-04:00'},
                              {'speed_to_lead_status':'No outbound call as of lookup'})
    assert result['after_hours_response']=='No outbound call as of lookup'
    assert result['after_hours_response_seconds'] is None
    assert result['after_hours_response_color']==''
    for hours in ({'days':[]},{'open':'17:00','close':'09:00'},{'timezone':'Invalid/Zone'}):
        with pytest.raises(ValueError): validate_hours(hours)


def test_csv_exports_both_original_and_after_hours_times_and_schedule():
    data=sample(); report=build(data,data['options']); row=report['rows'][0]
    row['created_at']='2026-10-23T18:00:00-04:00'
    report['meta']['timezone']='America/New_York'
    report['speed_results']={row['person_id']:{'first_called_at':'2026-10-26T09:05:00-04:00',
                                             'speed_to_lead_seconds':227100,'speed_to_lead':'63h 05m 00s'}}
    found=select_rows(report)[0]
    assert found['speed_to_lead']=='63h 05m 00s'
    assert found['after_hours_response']=='0h 05m 00s'
    exported=list(csv.DictReader(io.StringIO(export_parts(report)['people.csv'].decode('utf-8-sig'))))[0]
    assert exported['after_hours_lead']=='Yes'
    assert exported['next_workday_start']=='10/26/2026 09:00:00AM'
    assert exported['business_timezone']=='America/New_York'
    assert exported['after_hours_response_seconds']=='300.0'


def test_settings_save_recalculates_existing_report_without_refetch(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    next(b for b in app.button if b.label=='Load demo').click().run()
    job=app.session_state.speed_lookup
    assert 'after_hours_lead' in app.tabs[0].dataframe[1].value
    next(r for r in app.radio if r.label=='Menu').set_value('Settings').run()
    next(x for x in app.multiselect if x.label=='Business days').set_value([0,1,2,3,4,5])
    next(b for b in app.button if b.label=='Save business hours').click().run()
    assert not app.exception
    assert Store().settings()['business_hours']['days']==[0,1,2,3,4,5]
    next(r for r in app.radio if r.label=='Menu').set_value('Reports').run()
    assert not app.exception and app.session_state.speed_lookup is job
    assert app.session_state.report['meta']['business_hours']['days']==[0,1,2,3,4,5]
    for tab in app.tabs:
        table=tab.dataframe[1 if tab.label=='All UTM codes' else 0].value
        assert 'after_hours_response' in table
