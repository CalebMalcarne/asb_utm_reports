from datetime import datetime, timedelta, timezone
import json
import pytest
from api import Aircall, APIError
from aircall_search import fetch_calls, fetch_transcript_batch, tag_matches, export_search
from transcript_search import search_options


class Client:
    def __init__(self,start,end):
        self.start,self.end=start,end
        self.requested=[]
    def calls(self,number,start,end):
        assert number is None
        t=int(self.start.timestamp())+1
        return [
            dict(id=1,started_at=t,raw_digits='+12025550101',tags=[{'id':1,'name':'Qualified'}],
                 contact={'id':12,'first_name':'Mark','last_name':'Layman'}),
            dict(id=2,started_at=t,tags=[{'id':2,'name':'Unqualified'}]),
            dict(id=3,started_at=t,tags=[]),
            dict(id=4,started_at=int(self.end.timestamp()),tags=[]),
            dict(id=1,started_at=t,tags=[])]
    def transcript(self,cid):
        self.requested.append(cid)
        return dict(status='available',text='retirement protection')


def setup():
    end=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(days=1)
    start=end-timedelta(days=1)
    return Client(start,end),start,end


def test_all_calls_no_phone_filter_boundaries_dedup_and_tag_rules():
    client,start,end=setup()
    data=fetch_calls({},start,end,client=client)
    assert [c['call_id'] for c in data['calls']]==['1','2','3']
    assert data['calls'][0]['name']=='Mark Layman'
    assert all(c['transcript_status']=='not requested' for c in data['calls'])
    assert client.requested==[]
    filtered=fetch_calls({},start,end,['1'],client=client)
    assert [c['call_id'] for c in filtered['calls']]==['1']
    excluded=fetch_calls({},start,end,['1'],'Without selected tags',client=client)
    assert [c['call_id'] for c in excluded['calls']]==['2','3']
    assert tag_matches({'tags':[{'id':1}]},[],'Without selected tags')
    assert not tag_matches({'tags':[{'id':1}]},['1','2'],'All selected tags')
    assert tag_matches({'tags':[{'id':1},{'id':2}]},['1','2'],'All selected tags')


def test_batches_resume_without_refetching_and_keep_partial_work_on_failure():
    client,start,end=setup()
    data=fetch_calls({},start,end,client=client)
    first=fetch_transcript_batch(data,{},1,client=client)
    assert client.requested==['1']
    assert all(c['transcript_status']=='not requested' for c in data['calls'])
    def fail(cid): raise APIError('quota exceeded',429)
    client.transcript=fail
    failed=fetch_transcript_batch(first,{},2,client=client)
    assert failed['calls'][0]['transcript_status']=='available'
    assert failed['calls'][1]['transcript_status']=='not requested'
    assert failed['fetch_error']=='quota exceeded'
    client.transcript=lambda cid: dict(status='unavailable',text='')
    done=fetch_transcript_batch(failed,{},2,client=client)
    assert 'fetch_error' not in done
    assert [c['transcript_status'] for c in done['calls']]==['available','unavailable','unavailable']


def test_export_scope_selection_coverage_and_no_empty_files():
    client,start,end=setup()
    data=fetch_transcript_batch(fetch_calls({},start,end,client=client),{},1,client=client)
    opts=search_options('retirement')
    parts=export_search(data,opts)
    assert 'transcripts/Mark_Layman_1.txt' in parts
    assert 'highlighted_transcripts/Mark_Layman_1.html' in parts
    assert json.loads(parts['Aircall_search_settings.json'])['coverage']['not_searchable']==2
    parts=export_search(data,opts,selected_ids=[])
    assert set(parts)=={'Aircall_search_settings.json','Transcript_search_settings.json'}
    parts=export_search(data,search_options(),include_text=False,include_highlights=False)
    assert set(parts)=={'Aircall_Activity.csv','Aircall_search_settings.json'}
    assert json.loads(parts['Aircall_search_settings.json'])['exported_calls']==3


def test_api_all_calls_omit_phone_and_request_contacts():
    client=Aircall('example','fake')
    seen=[]
    client.get=lambda path,params: seen.append((path,params)) or {'calls':[]}
    client.search_page(None,1,2)
    assert 'phone_number' not in seen[0][1]
    assert seen[0][1]['fetch_contact']=='true'
    client.search_page('+12025550101',1,2)
    assert seen[1][1]['phone_number']=='+12025550101'


def test_paginated_tags_never_follow_external_next_link():
    client=Aircall('example','fake')
    seen=[]
    def get(path,params):
        seen.append((path,dict(params)))
        p=params['page']
        return {'tags':[{'id':p,'name':str(p)}],
                'meta':{'total':2,'next_page_link':'https://untrusted.invalid/' if p==1 else None}}
    client.get=get
    assert [t['id'] for t in client.tags()]==[1,2]
    assert [p for p,q in seen]==['tags','tags']
    client.get=lambda *args: {'tags':[{'id':1}],'meta':{'next_page_link':'next'}}
    with pytest.raises(APIError): client.tags()


def test_dense_account_wide_windows_and_fractional_end():
    from test_api import FakeAircall
    client=FakeAircall([{'id':i,'created_at':i} for i in range(10)])
    assert sorted(c['id'] for c in client.calls(None,0,9.5))==list(range(10))
