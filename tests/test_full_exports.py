import csv
import io
import zipfile
from datetime import datetime,timezone,date
from copy import deepcopy
from pypdf import PdfReader

from core import activity_dates,activity_window,parse_dt
from demo import sample,DemoAircall
from service import build,enrich,full_report_parts,zip_parts,transcript_filename


def rows(content): return list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
def pdf_text(content): return '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)
def populated():
    data=sample(); report=build(data,data['options'])
    report['meta'].update(name='Advertiser review',timezone='America/New_York',company='asb-example')
    report['rows'][0]['name']='Mark Layman'
    return enrich(report,{},['1','2','3','4','5'],parse_dt(data['start']),parse_dt(data['end']),client=DemoAircall())


def test_full_bundle_has_all_and_matching_datasets_and_named_transcripts():
    report=populated(); parts=full_report_parts(report)
    assert {'Aircall_Activity.csv','all_People.csv','Match_people.csv','Match_aircall_activity.csv','UTM_summary.csv','Overall Summary.pdf'}<=set(parts)
    assert {r['person_id'] for r in rows(parts['all_People.csv'])}=={'1','2','3','4','5'}
    assert {r['person_id'] for r in rows(parts['Match_people.csv'])}=={'1','2','4'}
    assert len(rows(parts['Aircall_Activity.csv']))==5
    assert {r['person_id'] for r in rows(parts['Match_aircall_activity.csv'])}=={'1','2','4'}
    assert 'transcripts/Mark_Layman_901.txt' in parts
    assert all('activity_start' in r and 'activity_end' in r for r in rows(parts['all_People.csv']))
    with zipfile.ZipFile(io.BytesIO(zip_parts(parts))) as z:
        assert set(z.namelist())==set(parts)
        assert z.read('Overall Summary.pdf').startswith(b'%PDF-')


def test_full_bundle_respects_utm_scope_and_omits_empty_files():
    report=populated(); key=report['rows'][4]['group_key'] # organic, no matching people
    parts=full_report_parts(report,[key])
    assert 'Match_people.csv' not in parts and 'Match_aircall_activity.csv' not in parts
    assert {r['person_id'] for r in rows(parts['all_People.csv'])}=={'5'}
    assert {r['person_id'] for r in rows(parts['Aircall_Activity.csv'])}=={'5'}
    assert not any('Mark_Layman' in name for name in parts)
    text=pdf_text(parts['Overall Summary.pdf'])
    assert 'instagram' in text and 'AFLT2421' not in text and 'Mark Layman' not in text
    report['calls']=[]
    parts=full_report_parts(report,[key])
    assert 'Aircall_Activity.csv' not in parts and not any(k.startswith('transcripts/') for k in parts)
    assert set(full_report_parts(report,[]))=={'Overall Summary.pdf'}


def test_pdf_identifies_report_tags_dates_percentages_and_qualified_or_matching_people():
    data=sample(); data['options']['tags']=['id:3'] # search Contacted; Qualified remains a separate metric
    report=build(data,data['options'])
    report['meta'].update(name='Named campaign report',company='company-example',timezone='America/New_York')
    text=pdf_text(full_report_parts(report,created_at=datetime(2026,10,20,12,tzinfo=timezone.utc))['Overall Summary.pdf'])
    for value in ['Named campaign report','company-example','2026-10-20','Contacted [id:3]','60.00%','20.00%',
                  'Qualified or matching people (4)','Example Person 1','Example Person 2','Example Person 3','Example Person 4','AFLT2421']:
        assert value in text
    assert 'Example Person 5' not in text
    assert 'None selected' in pdf_text(full_report_parts({**report,'options':{**report['options'],'stat_tags':{'qualified':[],'contacted':[]}}})['Overall Summary.pdf'])


def test_pdf_repeats_table_headers_and_preserves_long_values_over_multiple_pages():
    report=populated(); base=report['rows'][0]
    report['rows']=[{**deepcopy(base),'person_id':str(i),'name':f'Contact number {i}',
                     'utm_campaign':'Long campaign '+('campaign-content-'*20)} for i in range(60)]
    report['calls']=[]
    pdf=PdfReader(io.BytesIO(full_report_parts(report)['Overall Summary.pdf']))
    assert len(pdf.pages)>2
    text='\n'.join(p.extract_text() for p in pdf.pages)
    assert 'Contact number 59' in text
    assert text.count('Contact details')>=2


def test_transcript_filenames_are_safe_and_blank_transcripts_are_omitted():
    name=transcript_filename({'call_id':'../../901'},{'name':'../Mark <Layman> /','person_id':'1'})
    assert name=='transcripts/Mark_Layman_901.txt'
    assert transcript_filename({'call_id':'9'},{'name':'','person_id':'7'})=='transcripts/Unknown_Person_7_9.txt'
    report=populated(); report['calls'][0]['transcript']=' \n'
    assert 'transcripts/Mark_Layman_901.txt' not in full_report_parts(report)


def test_activity_defaults_preserve_exclusive_midnight_and_partial_day_bounds():
    meta={'start':'2026-10-05T04:00:00+00:00','end':'2026-10-12T04:00:00+00:00'}
    assert activity_dates(meta,'America/New_York')==(date(2026,10,5),date(2026,10,11))
    assert activity_window(meta,'America/New_York',*activity_dates(meta,'America/New_York'))==(parse_dt(meta['start']),parse_dt(meta['end']))
    meta['end']='2026-10-11T16:42:00+00:00'
    assert activity_window(meta,'America/New_York',date(2026,10,5),date(2026,10,11))[1]==parse_dt(meta['end'])
    start,end=activity_window(meta,'America/New_York',date(2026,10,5),date(2026,10,12),now=datetime(2026,10,20,tzinfo=timezone.utc))
    assert end.isoformat()=='2026-10-13T04:00:00+00:00'


def test_activity_defaults_handle_dst_day_length():
    meta={'start':'2026-03-08T05:00:00+00:00','end':'2026-03-09T04:00:00+00:00'}
    assert activity_dates(meta,'America/New_York')==(date(2026,3,8),date(2026,3,8))
    start,end=activity_window(meta,'America/New_York',*activity_dates(meta,'America/New_York'))
    assert (end-start).total_seconds()==23*3600


def test_optional_phrase_exports_preserve_standard_files_and_utm_scope():
    from transcript_search import search_options
    report=populated()
    key=report['rows'][4]['group_key']
    baseline=full_report_parts(report,[key])
    parts=full_report_parts(report,[key],transcript_search=search_options('synthetic example'))
    for name in baseline:
        if name!='Overall Summary.pdf': assert parts[name]==baseline[name]
    assert {r['person_id'] for r in rows(parts['Transcript_matches.csv'])}=={'5'}
    html_names=[name for name in parts if name.startswith('highlighted_transcripts/')]
    assert html_names==['highlighted_transcripts/Example_Person_5_905.html']
    assert '<mark>synthetic example</mark>' in parts[html_names[0]].decode()
    assert not any(name.startswith('highlighted_transcripts/') for name in baseline)
