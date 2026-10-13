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
