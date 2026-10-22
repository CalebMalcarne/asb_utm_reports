import csv
import io
from datetime import timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import parse_dt,csv_bytes
from demo import sample
from report_display import format_timestamp,table_rows,speed_to_lead,people_table,SPEED_COLORS
from service import build,select_rows,export_parts


def fixture(seconds=1200):
    row=dict(person_id='1',created_at='2026-10-22T14:00:00Z',
             activity_start='2026-10-22T00:00:00Z',activity_end='2026-10-24T00:00:00Z',
             aircall_status='complete')
    call=dict(person_id='1',call_id='100',direction='outbound',answered=False,
              started_at=(parse_dt(row['created_at'])+timedelta(seconds=seconds)).isoformat())
    return row,call


@pytest.mark.parametrize('seconds,color',[(0,'Green'),(299,'Green'),(300,'Green'),
    (300.5,'Yellow'),(301,'Yellow'),(599,'Yellow'),(600,'Yellow'),(600.5,'Red'),
    (601,'Red'),(3600,'Red'),(86400,'Red')])
def test_thresholds(seconds,color):
    row,call=fixture(seconds)
    result=speed_to_lead(row,[call])
    assert result['speed_to_lead_seconds']==seconds
    assert result['speed_to_lead_color']==color
    assert result['first_call_id']=='100'


def test_first_outbound_after_creation_across_numbers_and_unordered_calls():
    row,call=fixture()
    calls=[{**call,'call_id':'later','started_at':'2026-10-22T16:00:00Z'},
           {**call,'call_id':'inbound','direction':'inbound','started_at':'2026-10-22T14:01:00Z'},
           {**call,'call_id':'prior','started_at':'2026-10-22T13:59:00Z'},
           {**call,'person_id':'another','started_at':'2026-10-22T14:00:00Z'},call]
    result=speed_to_lead(row,calls)
    assert result['speed_to_lead_seconds']==1200
    assert result['first_call_id']=='100'
    assert result['speed_to_lead']=='0h 20m 00s'


@pytest.mark.parametrize('changes,status',[
    ({'aircall_status':'not requested'},'Not fetched'),
    ({'aircall_status':'shared phone — review'},'Unknown: shared phone — review'),
    ({'aircall_status':'no valid phone'},'Unknown: no valid phone'),
    ({'activity_start':'2026-10-22T14:10:00Z'},'Unknown: history does not cover creation'),
    ({'activity_end':'2026-10-22T14:00:00Z'},'Unknown: history does not cover creation')])
def test_unknown_history_is_never_given_a_speed_color(changes,status):
    row,call=fixture()
    result=speed_to_lead({**row,**changes},[call])
    assert result['speed_to_lead_status']==status
    assert result['speed_to_lead_seconds'] is None
    assert result['first_called_at'] is None
    assert result['speed_to_lead_color']==''


def test_no_calls_and_calls_outside_fetched_window_do_not_mean_zero_delay():
    row,call=fixture()
    for calls in ([],[{**call,'started_at':row['activity_end']}],[{**call,'direction':'inbound'}]):
        result=speed_to_lead(row,calls)
        assert result['speed_to_lead']=='No outbound call in fetched period'
        assert result['speed_to_lead_seconds'] is None


def test_dates_timezone_midnight_noon_and_dst_elapsed_are_correct():
    assert format_timestamp('2026-10-22T04:00:00Z','America/New_York')=='10/22/2026 12:00:00AM'
    assert format_timestamp('2026-10-22T16:00:00Z','America/New_York')=='10/22/2026 12:00:00PM'
    assert format_timestamp(None)==''
    row,call=fixture()
    row.update(created_at='2026-11-01T01:15:00-04:00',activity_start='2026-11-01T04:00:00Z',activity_end='2026-11-02T05:00:00Z')
    call['started_at']='2026-11-01T01:45:00-05:00'
    assert speed_to_lead(row,[call])['speed_to_lead_seconds']==5400
    source=[dict(created_at='2026-10-22T04:00:00Z',duration_seconds=125,transcript='[5] Hello')]
    display=table_rows(source,'America/New_York')
    assert display[0]['created_at']=='10/22/2026 12:00:00AM'
    assert display[0]['duration_seconds']==125 and display[0]['transcript']=='[5] Hello'
    assert source[0]['created_at'].endswith('Z')


def test_csv_and_scoped_report_include_speed_and_readable_dates():
    data=sample(); report=build(data,data['options']); row=report['rows'][0]
    created=parse_dt(row['created_at'])
    row.update(activity_start=data['start'],activity_end=data['end'],aircall_status='complete')
    report['meta']['timezone']='America/New_York'
    report['calls']=[dict(person_id=row['person_id'],call_id='1',direction='outbound',
                          started_at=(created+timedelta(minutes=5)).isoformat())]
    parts=export_parts(report,[row['group_key']])
    people=list(csv.DictReader(io.StringIO(parts['people.csv'].decode('utf-8-sig'))))
    found=next(r for r in people if r['person_id']==row['person_id'])
    assert found['speed_to_lead_color']=='Green'
    assert float(found['speed_to_lead_seconds'])==300
    assert found['created_at']==format_timestamp(row['created_at'],'America/New_York')
    assert found['report_timezone']=='America/New_York'
    assert row['created_at']==created.isoformat() # raw calculation data retained
    report['calls']=[]
    assert select_rows(report,[row['group_key']])[0]['speed_to_lead_seconds'] is None


def test_speed_cell_style_is_correct_and_other_cells_are_not_colored():
    styled=people_table([{'name':'Mark','speed_to_lead':'0h 05m 00s','speed_to_lead_color':'Green'}])
    styles=styled._compute().ctx
    assert ('background-color',SPEED_COLORS['Green'][0]) in styles[(0,1)]
    assert (0,0) not in styles


def test_streamlit_people_table_and_call_table_show_formatted_dates(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
    next(b for b in app.button if b.label=='Load demo').click().run()
    next(b for b in app.button if b.label=='Fetch calls & transcripts').click().run()
    assert not app.exception
    people=app.tabs[0].dataframe[1].value
    assert 'speed_to_lead' in people
    assert people['created_at'].str.match(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}(AM|PM)').all()
    assert people.loc[people['person_id']=='1','speed_to_lead_color'].iloc[0]=='Red'
    calls=app.tabs[0].dataframe[2].value
    assert calls['started_at'].str.contains('/').all()
