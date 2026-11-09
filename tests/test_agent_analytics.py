import copy
import csv
import io
import json
from pypdf import PdfReader
import pytest
from agent_analytics import analyze_agents,report_parts,transcript_parts
from service import build
from demo import sample
from core import parse_dt


def fixture():
    data=sample(); report=build(data,data['options'])
    report['meta'].update(start='2026-11-01T00:00:00Z',end='2026-11-09T00:00:00Z',timezone='America/New_York')
    report['rows'][4]['phones']=report['rows'][3]['phones'][:]
    first={'1':('11','2026-11-02T15:05:00Z'),'2':('12','2026-11-02T12:00:00Z'),'3':('13','2026-11-02T15:02:00Z')}
    report['speed_results']={}
    for p in report['rows']:
        p['created_at']='2026-11-02T00:00:00Z' if p['person_id']=='2' else '2026-11-02T15:00:00Z'
        if p['person_id'] in first:
            cid,called=first[p['person_id']]
            report['speed_results'][p['person_id']]={'first_call_id':cid,'first_called_at':called,
                'speed_to_lead_seconds':(parse_dt(called)-parse_dt(p['created_at'])).total_seconds()}
    def call(cid,person,agent,when,direction='outbound'):
        return dict(call_id=cid,phone=report['rows'][person-1]['phones'][0] if person else '+12025550999',
            agent_id=agent,agent='Same Name' if agent in ('A','B') else 'Other Agent',name='Example Contact '+str(person),
            started_at=when,direction=direction,answered=True,duration_seconds=180,answered_to_end_seconds=120,
            transcript_status='available',transcript='Transcript '+cid)
    calls=[call('11',1,'A',first['1'][1]),call('12',2,'B',first['2'][1]),call('13',3,'B',first['3'][1]),
           call('14',2,'A','2026-11-02T16:00:00Z'),call('15',1,'C','2026-11-02T17:00:00Z','inbound'),
           call('16',0,'','2026-11-02T18:00:00Z'),call('17',4,'D','2026-11-02T19:00:00Z')]
    calls.extend([copy.deepcopy(calls[0]),call('old',1,'A','2026-11-01T23:59:59Z'),call('end',1,'A','2026-11-09T00:00:00Z')])
    dataset={'calls':calls,'meta':{'start':'2026-11-02T00:00:00Z','end':'2026-11-09T00:00:00Z','timezone':'America/New_York','fetched_at':'2026-11-09T15:00:00Z','demo':True}}
    return dataset,report


def test_agents_separated_by_id_first_response_credit_and_tied_ranks():
    dataset,report=fixture(); result=analyze_agents(dataset,report)
    agents={a['agent_id']:a for a in result['agents']}
    assert len(result['calls'])==7 and len(result['agents'])==5
    assert agents['A']['calls_made']==agents['B']['calls_made']==2
    assert agents['A']['calls_rank']==agents['B']['calls_rank']==1
    assert agents['D']['calls_rank']==3
    assert agents['A']['response_rank']==1 and agents['B']['response_rank']==2
    assert agents['B']['workday_response_rank']==1
    assert agents['A']['workday_average_seconds']==300
    assert agents['B']['workday_average_seconds']==120
    assert agents['B']['after_hours_average_seconds']==43200
    assert agents['B']['average_response_seconds']==21660
    assert agents['A']['total_call_seconds']==360
    assert agents['A']['total_answered_to_end_seconds']==240
    assert agents['C']['calls_made']==0 and agents['C']['calls_received']==1
    assert agents['C']['calls_rank'] is None and agents['D']['response_rank'] is None
    assert agents['']['calls_rank'] is None
    assert result['meta']['verified_responses']==3
    by_call={c['call_id']:c for c in result['calls']}
    assert not by_call['14']['response_included'] # follow-up by a different agent
    assert 'Shared phone' in by_call['17']['response_match']
    assert not by_call['15']['response_included'] # inbound
    assert [a['agent_id'] for a in analyze_agents(dataset,report,'Workday response time')['agents']][:2]==['B','A']


def test_first_call_outside_period_unverified_and_mismatch_are_excluded():
    dataset,report=fixture()
    report['speed_results']['1']['first_call_id']='old'
    report['speed_results']['2']['first_called_at']='2026-11-02T12:01:00Z'
    report['speed_results'].pop('3')
    result=analyze_agents(dataset,report)
    assert result['meta']['verified_responses']==0
    assert all(a['response_rank'] is None for a in result['agents'])
    assert all(c['response_seconds'] is None for c in result['calls'])
    assert len(result['calls'])==7


def test_no_pipedrive_and_demo_isolation():
    dataset,report=fixture()
    assert analyze_agents(dataset)['meta']['response_cohort_people']==0
    assert all(a['average_response_seconds'] is None for a in analyze_agents(dataset)['agents'])
    report['meta']['demo']=False
    with pytest.raises(ValueError,match='cannot be mixed'): analyze_agents(dataset,report)


def csv_rows(data): return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def test_all_and_single_agent_exports_preserve_global_ranks_and_transcript_scope():
    dataset,report=fixture(); analysis=analyze_agents(dataset,report)
    all_parts=report_parts(analysis)
    assert len(csv_rows(all_parts['Agent_rankings.csv']))==5
    single=report_parts(analysis,'id:B')
    ranking=csv_rows(single['Agent_rankings.csv'])
    assert len(ranking)==1 and ranking[0]['response_rank']=='2'
    assert ranking[0]['response_rank_of']=='2'
    assert {r['call_id'] for r in csv_rows(single['Calls.csv'])}=={'12','13'}
    assert {r['call_id'] for r in csv_rows(single['Lead_response_times.csv'])}=={'12','13'}
    assert json.loads(single['Report_settings.json'])['exported_agent_key']=='id:B'
    text='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(single['Agent_Report.pdf'])).pages)
    assert '#2 of 2' in text and 'Ranking within all agents' in text
    assert 'Example Contact 1' not in text
    transcripts=transcript_parts(analysis,'id:B')
    texts=[(path,value) for path,value in transcripts.items() if path.endswith('.txt')]
    assert len(texts)==2 and {value for _,value in texts}=={b'Transcript 12',b'Transcript 13'}
    assert all(path.endswith(('_12.txt','_13.txt')) for path,_ in texts)
    assert {r['call_id'] for r in csv_rows(transcripts['Transcript_manifest.csv'])}=={'12','13'}


def test_zero_call_response_and_missing_duration_do_not_hide_data():
    dataset,report=fixture()
    called=report['speed_results']['1']['first_called_at']
    report['rows'][0]['created_at']=called; report['speed_results']['1']['speed_to_lead_seconds']=0
    dataset['calls'][0]['duration_seconds']=None
    result=analyze_agents(dataset,report)
    a=next(a for a in result['agents'] if a['agent_id']=='A')
    assert a['workday_average_seconds']==0 and a['response_rank']==1
    assert a['average_call_seconds']==180
