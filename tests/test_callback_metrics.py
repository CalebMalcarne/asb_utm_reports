from datetime import timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from callback_metrics import callback_stats,duration_label
from core import parse_dt
from demo import sample
from speed_lookup import SpeedLookup


def person(seconds,overnight=False,adjusted=None):
    return dict(after_hours_lead='Yes' if overnight else 'No',
                first_called_at='2026-10-22T13:05:00Z',speed_to_lead_seconds=seconds,
                after_hours_response_seconds=adjusted)


def test_weighted_overall_uses_adjusted_overnight_values_and_separate_categories():
    rows=[person(60),person(120),person(600),person(50000,True,300),person(60000,True,0),
          person(None),{**person(90),'first_called_at':None},person(70000,True,None)]
    stats=callback_stats(rows)
    assert stats['overall']['average_seconds']==216
    assert stats['overall']['count']==5 and stats['overall']['excluded']==3
    assert stats['workday']['average_seconds']==260
    assert stats['workday']['shortest_seconds']==60
    assert stats['after_hours']['average_seconds']==150
    assert stats['after_hours']['shortest_seconds']==0
    assert stats['overall']['mode_minutes']==[]
    assert stats['overall']['mode_count']==0


def test_minute_ranges_boundaries_ties_and_no_false_mode():
    stats=callback_stats([person(n) for n in [59.9,60,60.1,119.9,120,120,120]])['overall']
    assert stats['mode_minutes']==[1,2] and stats['mode_count']==3
    assert callback_stats([person(60),person(120)])['overall']['mode_minutes']==[]
    assert callback_stats([person(60)])['overall']['mode_minutes']==[]
    assert duration_label(3599.6)=='1h 00m 00s'


@pytest.mark.parametrize('invalid',[None,-1,float('nan'),float('inf'),True,'300'])
def test_invalid_or_missing_measurements_are_excluded(invalid):
    stats=callback_stats([person(invalid),person(5000,True,invalid)])
    assert stats['overall']['count']==0
    assert stats['overall']['average_seconds'] is None
    assert stats['overall']['shortest_seconds'] is None
    assert duration_label(None)=='—'
    assert callback_stats([])['overall']['total']==0


def test_streamlit_metrics_update_with_lookup_and_respect_utm_scope(tmp_path,monkeypatch):
    monkeypatch.setenv('ASB_REPORTS_DATA_DIR',str(tmp_path))
    # Hold background work, then supply known first-call results as a completed lookup.
    monkeypatch.setattr(SpeedLookup,'start',lambda self:None)
    data=sample(); start=parse_dt(data['start'])
    for index,p in enumerate(data['people']):
        p['add_time']=(start+timedelta(hours=10 if index<2 else 18)).isoformat()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30)
    app.session_state['data']=data
    app.run()
    assert not app.exception
    assert [m.value for m in app.tabs[0].metric][4:]==['—']*9
    job=app.session_state.speed_lookup
    offsets={'1':60,'2':120,'3':300,'4':600}
    for row in app.session_state.report['rows']:
        pid=row['person_id']
        if pid not in offsets: continue
        created=parse_dt(row['created_at'])
        after=pid in ('3','4')
        baseline=start+timedelta(days=1,hours=9) if after else created
        called=baseline+timedelta(seconds=offsets[pid])
        job.results[pid]={'first_called_at':called.isoformat(),'speed_to_lead_seconds':(called-created).total_seconds()}
    job.done=True
    app.run()
    assert not app.exception
    values=[m.value for m in app.tabs[0].metric]
    assert values[4:7]==['0h 04m 30s','0h 01m 00s','No repeated range']
    assert values[7:10]==['0h 01m 30s','0h 01m 00s','No repeated range']
    assert values[10:13]==['0h 07m 30s','0h 05m 00s','No repeated range']
    meta=next(t for t in app.tabs if t.label.startswith('meta /'))
    assert meta.metric[4].value=='0h 07m 30s'
    assert meta.metric[7].value=='—'
    app.tabs[0].checkbox[0].check().run()
    assert not app.exception
    assert [m.value for m in app.tabs[0].metric]==values
    assert app.session_state.speed_lookup is job
