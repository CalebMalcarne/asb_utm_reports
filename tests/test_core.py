from datetime import datetime,date,timezone
from copy import deepcopy
from core import *
from demo import sample
from service import build,select_rows,export_parts

NOW=datetime(2026,10,13,19,tzinfo=timezone.utc)

def test_periods_and_dst():
    s,e,a,b=period('Last week','America/New_York',NOW)
    assert (a,b)==(date(2026,10,5),date(2026,10,11))
    assert s.isoformat()=='2026-10-05T04:00:00+00:00'
    s,e,a,b=period('Last month','America/New_York',NOW)
    assert (a,b)==(date(2026,9,1),date(2026,9,30))
    s,e,*_=period('Custom','America/New_York',NOW,(date(2026,3,8),date(2026,3,8)))
    assert (e-s).total_seconds()==23*3600
    s,e,*_=period('Today','America/New_York',NOW)
    assert e==NOW

def test_custom_fields_native_tags_and_correct_denominator():
    data=sample(); report=build(data,data['options'])
    assert len(report['rows'])==5 # no UTM omitted
    assert sum(r['matches_tag'] for r in report['rows'])==3
    groups=summarize(report['rows'],report['catalog'])
    assert len(groups)==3
    meta=next(g for g in groups if 'meta' in g['utm_group'])
    assert meta['people']==2 and meta['matching_people']==1 and meta['match_pct']==50
    parts=export_parts(report,[meta['group_key']],True)
    assert '50.0' in parts['utm_summary.csv'].decode()
    assert len(list(csv.DictReader(io.StringIO(parts['people.csv'].decode('utf-8-sig')))))==1

def test_text_tags_do_not_substring_match():
    data=sample(); options={**data['options'],'tag_field':'demo_tags','tags':['text:qualified']}
    data['people'][2]['custom_fields']['demo_tags']='Unqualified'
    report=build(data,options)
    assert not next(r for r in report['rows'] if r['person_id']=='3')['matches_tag']
    assert sum(r['matches_tag'] for r in report['rows'])==3

def test_enum_labels_and_duplicate_person_ids():
    fields=[{'field_code':'tag','field_type':'set','options':[{'id':7,'label':'Client'}]}]
    assert tag_entries({'custom_fields':{'tag':[7]}},'tag',fields)=={'id:7':'Client'}
    assert tag_entries({'custom_fields':{'tag':[{'id':7,'label':'Client'}]}},'tag',fields)=={'id:7':'Client'}
    data=sample();data['people'].append(deepcopy(data['people'][0]))
    assert len(build(data,data['options'])['rows'])==5

def test_all_tags_and_empty_group_selection():
    data=sample();options={**data['options'],'tags':['id:1','id:2'],'tag_rule':'All selected tags'}
    report=build(data,options)
    assert sum(r['matches_tag'] for r in report['rows'])==1
    assert select_rows(report,[])==[]
    assert len(select_rows(report,None))==5

def test_csv_protects_formulas_and_keeps_utf8():
    result=csv_bytes([{'name':'=CMD()','phone':'+12025550101','note':'café'}]).decode('utf-8-sig')
    assert "'=CMD()" in result and "'+12025550101" in result and 'café' in result

def test_campaign_values_containing_separator_stay_distinct():
    data=sample()
    data['people'][0]['custom_fields'].update(demo_source='a / b',demo_campaign='c')
    data['people'][1]['custom_fields'].update(demo_source='a',demo_campaign='b / c')
    report=build(data,data['options'])
    assert report['rows'][0]['group_key']!=report['rows'][1]['group_key']

def test_null_option_metadata_and_native_label_objects():
    fields=[{'field_code':'text_tags','field_type':'text','options':None}]
    p={'custom_fields':{'text_tags':'Qualified, Client'}}
    assert tag_catalog(fields,[p],'text_tags')=={'text:client':'Client','text:qualified':'Qualified'}
    assert tag_entries({'labels':[{'id':9,'name':'Qualified'}]},'label_ids',[])=={'id:9':'Qualified'}

def read_csv(content):
    return list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))

def test_group_stat_percentages_and_csv_denominator_are_independent_of_detail_filter():
    data=sample(); report=build(data,data['options'])
    group=next(r for r in summarize(report['rows'],report['catalog']) if json.loads(r['group_key'])[0]=='meta')
    assert (group['qualified_people'],group['contacted_people'])==(1,1)
    assert (group['qualified_pct'],group['contacted_pct'])==(50,50)
    parts=export_parts(report,[group['group_key']],matched_only=True)
    summary=read_csv(parts['utm_summary.csv'])
    people=read_csv(parts['people.csv'])
    assert len(summary)==len(people)==1
    assert summary[0]['people']=='2'
    assert summary[0]['qualified_pct']==summary[0]['contacted_pct']=='50.0'
    assert people[0]['person_id']=='4'
    assert people[0]['group_people']=='2'
    assert people[0]['group_qualified_pct']==people[0]['group_contacted_pct']=='50.0'

def test_multiple_statistic_tags_count_each_person_once_and_do_not_change_report_matching():
    data=sample(); options={**data['options'],'stat_tags':{'qualified':['id:1','id:2'],'contacted':['id:1','id:3']}}
    report=build(data,options)
    groups=summarize(report['rows'],report['catalog'],report['options']['stat_tags'])
    affiliate=next(g for g in groups if json.loads(g['group_key'])[0]=='AFLT2421')
    assert affiliate['qualified_people']==2 # the person with both labels counts once
    assert affiliate['qualified_pct']==affiliate['contacted_pct']==100
    meta=next(g for g in groups if json.loads(g['group_key'])[0]=='meta')
    assert meta['matching_people']==1 and meta['contacted_people']==2

def test_unconfigured_statistics_are_blank_and_configured_without_matches_are_zero():
    data=sample(); options={**data['options'],'stat_tags':{'qualified':[],'contacted':['id:3']}}
    report=build(data,options)
    key=next(r['group_key'] for r in report['rows'] if r['utm_source']=='instagram')
    parts=export_parts(report,[key],matched_only=True)
    summary=read_csv(parts['utm_summary.csv'])[0]
    assert summary['qualified_pct']==summary['qualified_people']==''
    assert summary['contacted_people']=='0' and summary['contacted_pct']=='0.0'
    assert read_csv(parts['people.csv'])==[]
    assert 'group_qualified_pct' in parts['people.csv'].decode('utf-8-sig')
    assert statistic_tags({'text:unqualified':'Unqualified'})['qualified']==[]

def test_group_labels_and_filenames_do_not_merge_delimiter_collisions():
    data=sample()
    data['people'][0]['custom_fields'].update(demo_source='a / b',demo_campaign='c')
    data['people'][1]['custom_fields'].update(demo_source='a',demo_campaign='b / c')
    report=build(data,data['options']); tabs=group_tabs(summarize(report['rows']))
    assert len(tabs)==4
    assert len(set(tabs.values()))==4
    assert len({group_filename(k) for k in tabs.values()})==4
    assert all('/' not in group_filename(k) for k in tabs.values())

def test_exports_omit_other_groups_calls_transcripts_and_matching_issues():
    data=sample(); report=build(data,data['options'])
    report['calls']=[{'person_id':str(i),'call_id':i,'transcript':str(i),'group_key':report['rows'][i-1]['group_key']} for i in (1,3)]
    report['issues']=[{'person_id':str(i),'issue':'example'} for i in (1,3)]
    parts=export_parts(report,[report['rows'][0]['group_key']])
    assert [r['person_id'] for r in read_csv(parts['aircall_activity.csv'])]==['1']
    assert 'transcripts/call_1.txt' in parts and 'transcripts/call_3.txt' not in parts
    assert [r['person_id'] for r in json.loads(parts['report_settings.json'])['issues']]==['1']
