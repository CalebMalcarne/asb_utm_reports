from datetime import datetime,timezone
import io
from pypdf import PdfReader

from core import parse_dt
from demo import sample
from report_display import speed_to_lead
from service import build,full_report_parts


def report_fixture():
    data=sample(); report=build(data,data['options'])
    report['meta'].update(name='Speed to Lead review',timezone='America/New_York')
    times={
        '1':('2026-10-23T10:00:00-04:00','2026-10-23T10:05:00-04:00'),
        '2':('2026-10-23T19:00:00-04:00','2026-10-24T19:30:00-04:00'),
        '3':('2026-10-23T10:00:00-04:00','2026-10-23T10:10:00-04:00'),
        '4':('2026-10-24T15:00:00-04:00',None),
        '5':('2026-10-24T16:00:00-04:00',None)}
    report['speed_results']={}
    for row in report['rows']:
        created,called=times[row['person_id']]; row['created_at']=created
        end='2026-10-26T12:00:00-04:00'
        result=speed_to_lead({**row,'activity_start':created,'activity_end':end,'aircall_status':'complete'},
                             [{'person_id':row['person_id'],'call_id':row['person_id'],'direction':'outbound','started_at':called}] if called else [])
        if row['person_id']=='5': result={'speed_to_lead':'Loading…','speed_to_lead_status':'Loading…'}
        report['speed_results'][row['person_id']]=result
    return report


def pdf_text(report,groups=None):
    content=full_report_parts(report,groups,created_at=datetime(2026,10,26,18,tzinfo=timezone.utc))['Overall Summary.pdf']
    return '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)


def test_pdf_contains_callback_summaries_rules_deadlines_and_statuses():
    text=pdf_text(report_fixture())
    for expected in ['Speed to Lead','Average callback','Shortest callback','Most frequent callback',
                     'Workday leads','Overnight / after-hours','8h 15m 00s','0h 07m 30s','24h 30m 00s',
                     'Mon 09:00-18:00','Sat 11:00-14:00','24 elapsed hours from arrival',
                     '1 late callbacks; 1 overdue without a call','Pending first-call lookups: 1',
                     'Late callback - over 24h','Overdue - no call as of lookup',
                     '10/24/2026 07:00:00PM','10/25/2026 03:00:00PM','Green','Red']:
        assert expected in text
    assert 'Example Person 5' not in text # summaries include everyone; detail rows retain qualified/matched scope


def test_pdf_callback_stats_and_details_respect_selected_utm_group():
    report=report_fixture(); key=report['rows'][0]['group_key']
    text=pdf_text(report,[key])
    assert '12h 17m 30s' in text
    assert '1 late callbacks; 0 overdue without a call' in text
    assert 'Example Person 4' not in text
    assert 'retirement_october' not in text


def test_pdf_empty_scope_and_unknown_callbacks_are_explicit():
    report=report_fixture(); report['speed_results']={}
    text=pdf_text(report)
    assert 'Not available' in text and 'Not fetched' in text
    assert '0 late callbacks; 0 overdue without a call' in text
    empty=pdf_text(report,[])
    assert 'No qualified or matching people in this scope' in empty
