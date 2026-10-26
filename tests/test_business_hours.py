import csv
import io
from datetime import timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from business_hours import after_hours_details,validate_hours
from demo import sample
from service import build,select_rows,export_parts
from storage import Store
from core import parse_dt


@pytest.mark.parametrize('created,flag,opening',[
    ('2026-10-22T08:59:59-04:00','Yes','2026-10-22T13:00:00+00:00'),
    ('2026-10-22T09:00:00-04:00','No',None),
    ('2026-10-22T16:59:59-04:00','No',None),
    ('2026-10-22T17:00:00-04:00','No',None),
    ('2026-10-22T18:00:00-04:00','Yes','2026-10-23T13:00:00+00:00'),
    ('2026-10-23T18:00:00-04:00','Yes','2026-10-24T15:00:00+00:00'),
    ('2026-10-24T10:59:59-04:00','Yes','2026-10-24T15:00:00+00:00'),
    ('2026-10-24T11:00:00-04:00','No',None),
    ('2026-10-24T13:59:59-04:00','No',None),
    ('2026-10-24T14:00:00-04:00','Yes','2026-10-26T13:00:00+00:00'),
    ('2026-10-25T12:00:00-04:00','Yes','2026-10-26T13:00:00+00:00'),
])
def test_arrival_boundaries_and_next_workday(created,flag,opening):
    result=after_hours_details({'created_at':created},{'speed_to_lead_status':'Loading…'})
    assert result['after_hours_lead']==flag
    assert result['next_workday_start']==opening
    assert result['after_hours_response']==('Loading…' if flag=='Yes' else 'Not applicable')
    assert result['after_hours_response_seconds'] is None


@pytest.mark.parametrize('seconds,color',[(0,'Green'),(300,'Green'),(601,'Green'),(86399,'Green'),(86400,'Green'),(86400.5,'Red'),(86401,'Red')])
def test_after_hours_response_and_colors(seconds,color):
    created=parse_dt('2026-10-21T18:00:00-04:00')
    result=after_hours_details({'created_at':created.isoformat()},
                              {'first_called_at':(created+timedelta(seconds=seconds)).isoformat()})
    assert result['after_hours_response_seconds']==seconds
    assert result['after_hours_response_color']==color
    assert result['speed_to_lead_color']==color
    assert result['response_deadline']==(created+timedelta(hours=24)).isoformat()


def test_dst_weekend_and_custom_workdays():
    result=after_hours_details({'created_at':'2026-10-31T18:00:00-04:00'},
                              {'first_called_at':'2026-11-01T17:00:00-05:00'})
    assert result['next_workday_start']=='2026-11-02T14:00:00+00:00'
    assert result['after_hours_response_seconds']==86400
    assert result['response_deadline']=='2026-11-01T22:00:00+00:00'
    assert result['response_status']=='Called within 24h'
    result=after_hours_details({'created_at':'2026-10-24T08:00:00-07:00'},
                              {'first_called_at':'2026-10-24T10:07:00-07:00'},
                              {'days':[5],'open':'10:00','close':'18:00','timezone':'America/Los_Angeles'})
    assert result['next_workday_start']=='2026-10-24T17:00:00+00:00'
    assert result['after_hours_response_seconds']==7620


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
    assert found['after_hours_response']=='63h 05m 00s'
    assert found['speed_to_lead_color']=='Red'
    exported=list(csv.DictReader(io.StringIO(export_parts(report)['people.csv'].decode('utf-8-sig'))))[0]
    assert exported['after_hours_lead']=='Yes'
    assert exported['next_workday_start']=='10/24/2026 11:00:00AM'
    assert exported['response_deadline']=='10/24/2026 06:00:00PM'
    assert exported['business_timezone']=='America/New_York'
    assert exported['after_hours_response_seconds']=='227100.0'


@pytest.mark.parametrize('seconds,overdue',[(86399,False),(86400,False),(86401,True)])
def test_uncalled_deadline_requires_verified_history(seconds,overdue):
    created=parse_dt('2026-10-24T15:00:00-04:00')
    speed={'speed_to_lead_status':'No outbound call as of lookup',
           'first_call_checked_through':(created+timedelta(seconds=seconds)).isoformat()}
    result=after_hours_details({'created_at':created.isoformat()},speed)
    assert ('Overdue' in result['response_status'])==overdue
    assert result['after_hours_response_color']==('Red' if overdue else '')
    assert result['after_hours_response_seconds'] is None
    speed['speed_to_lead_status']='Aircall lookup failed (HTTP 401)'
    result=after_hours_details({'created_at':created.isoformat()},speed)
    assert result['after_hours_response_color']==''
    assert 'Overdue' not in result['response_status']


def test_saved_old_default_is_migrated_but_custom_schedule_survives(tmp_path):
    store=Store(tmp_path); settings=store.settings()
    settings['business_hours']={'days':[0,1,2,3,4],'open':'09:00','close':'17:00','timezone':'America/New_York'}
    store.save_settings(settings)
    hours=store.settings()['business_hours']
    assert hours['close']=='18:00' and hours['days']==[0,1,2,3,4,5]
    assert hours['overrides']['5']=={'open':'11:00','close':'14:00'}
    settings['business_hours']['open']='08:00'; store.save_settings(settings)
    assert store.settings()['business_hours']['open']=='08:00'


def test_metadata_lookup_produces_evidence_for_overdue_uncalled_lead():
    from speed_lookup import SpeedLookup
    data=sample(); report=build(data,data['options'])
    report['rows']=report['rows'][:1]
    row=report['rows'][0]; row['created_at']='2026-10-23T19:00:00-04:00'
    class NoCalls:
        def calls(self,*args): return []
    job=SpeedLookup(report,{},NoCalls(),now=parse_dt('2026-10-25T12:00:00-04:00'))
    job._run(); results,done=job.snapshot()
    assert done and results[row['person_id']]['first_call_checked_through']
    report['speed_results']=results
    lead=select_rows(report)[0]
    assert lead['response_status']=='Overdue — no call as of lookup'
    assert lead['speed_to_lead_color']=='Red'
    assert lead['after_hours_response_seconds'] is None


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
