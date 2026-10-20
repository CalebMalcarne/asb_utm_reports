import csv
import io
import json
import pytest
from transcript_search import (search_options, search_text, analyze_calls, coverage,
                               highlighted_html, search_export_parts)


def call(cid='1', text='Retirement protection. Please call me.', status='available'):
    return dict(call_id=cid, name='Mark Layman', phone='+12025550101',
                transcript=text, transcript_status=status)


def test_phrases_literal_case_whitespace_boundaries_and_rule():
    opts=search_options('retirement protection\ncall me\nRETIREMENT PROTECTION',rule='all')
    assert len(opts['terms'])==2
    assert search_text('RETIREMENT \n protection. Call me!',opts)['matches']
    assert not search_text('Retirement protection only.',opts)['matches']
    assert search_text('Retirement protection only.',search_options(opts['terms']))['matches']
    assert not search_text('uncalled',search_options('call'))['matches']
    assert search_text('uncalled',search_options('call',whole_words=False))['matches']
    assert not search_text('CALL',search_options('call',case_sensitive=True))['matches']
    assert search_text('literal [a+b] here',search_options('[a+b]'))['matches']
    assert not search_text('aaab',search_options('[a+b]'))['matches']


def test_overlapping_matches_and_untrusted_text_are_safe_html():
    c=call(text='retirement protection <script>alert("x")</script> & account')
    c['name']='<img src=x onerror=alert(1)>'
    doc=highlighted_html(c,search_options('retirement\nretirement protection'))
    assert '<mark>retirement protection</mark>' in doc
    assert doc.count('<mark>')==1
    assert '<script>' not in doc and '<img src=x' not in doc
    assert '&lt;script&gt;' in doc and '&amp; account' in doc
    assert 'Content-Security-Policy' in doc


def test_unavailable_blank_and_unrequested_are_not_negative_matches():
    evaluated=analyze_calls([call(),call('2','other text'),call('3','','unavailable'),
                            call('4','','not requested'),call('5','  ')],search_options('retirement'))
    assert [x['search_status'] for x in evaluated]==['match','no match','not searchable','not searchable','not searchable']
    assert coverage(evaluated)['searchable']==2
    assert coverage(evaluated)['matching_calls']==1
    assert coverage(evaluated)['not_searchable']==3


def test_search_export_only_highlights_matching_calls_and_omits_empty_csv():
    opts=search_options('retirement')
    parts=search_export_parts([call(),call('2','other text'),call('3','','forbidden')],opts)
    assert set(parts)=={'Transcript_matches.csv','highlighted_transcripts/Mark_Layman_1.html','Transcript_search_settings.json'}
    rows=list(csv.DictReader(io.StringIO(parts['Transcript_matches.csv'].decode('utf-8-sig'))))
    assert rows[0]['call_id']=='1' and rows[0]['matched_phrases']=='retirement'
    meta=json.loads(parts['Transcript_search_settings.json'])
    assert meta['coverage']['calls']==3 and meta['coverage']['not_searchable']==1
    empty=search_export_parts([call('2','other text')],opts)
    assert set(empty)=={'Transcript_search_settings.json'}
    assert set(search_export_parts([call()],search_options()))=={'Transcript_search_settings.json'}


def test_query_limits_and_non_mutation():
    with pytest.raises(ValueError): search_options('\n'.join(str(i) for i in range(51)))
    with pytest.raises(ValueError): search_options('x'*301)
    with pytest.raises(ValueError): search_options('x',rule='invalid')
    c=call(); original=dict(c)
    search_export_parts([c],search_options('retirement'))
    assert c==original


def test_csv_formula_escaping_for_search_fields():
    c=call(text='=SUM(1+2)')
    c['name']='=danger'
    parts=search_export_parts([c],search_options('=SUM(1+2)'))
    rows=list(csv.DictReader(io.StringIO(parts['Transcript_matches.csv'].decode('utf-8-sig'))))
    assert rows[0]['name']=="'=danger"
    assert rows[0]['matched_phrases']=="'=SUM(1+2)"
