from datetime import datetime,timezone
import pytest
from api import Pipedrive,Aircall,APIError
from core import parse_dt

class FakeAircall(Aircall):
    CAP,PAGE_SIZE=4,2
    def __init__(self,records): self.records=records
    def search_page(self,n,lo,hi,page=1):
        records=[r for r in self.records if lo<=r['created_at']<=hi]
        return {'calls':records[(page-1)*2:page*2],'meta':{'total':len(records),'next_page_link':'next' if page*2<min(len(records),self.CAP) else None}}

def test_dense_phone_windows_and_boundary_seconds():
    client=FakeAircall([{'id':i,'created_at':i} for i in range(10)])
    assert sorted(c['id'] for c in client.calls('+12025550101',0,10))==list(range(10))

def test_unsplittable_window_fails():
    client=FakeAircall([{'id':i,'created_at':1} for i in range(5)])
    with pytest.raises(APIError): client.calls('n',1,2)

def test_creation_not_updated_time_and_cursor():
    client=Pipedrive('example','fake')
    queries=[]
    def get(path,params):
        queries.append(dict(params))
        if not params.get('cursor'):
            return {'data':[{'id':1,'add_time':'2026-10-14T00:00:00Z'},{'id':2,'add_time':'2026-10-12T05:00:00Z'}], 'additional_data':{'next_cursor':'next'}}
        return {'data':[{'id':3,'add_time':'2026-10-10T00:00:00Z'}],'additional_data':{'next_cursor':None}}
    client.get=get
    people=client.people(parse_dt('2026-10-12T04:00:00Z'),parse_dt('2026-10-13T04:00:00Z'),['utm'])
    assert [p['id'] for p in people]==[2]
    assert 'updated_since' not in queries[0]
    assert queries[1]['cursor']=='next'

def test_transcript_content_and_missing_status():
    client=Aircall('id','token')
    client.get=lambda *a: {'transcription':{'content':{'utterances':[{'participant_type':'internal','user_id':7,'start_time':1.2,'text':'Hello'}]}}}
    assert client.transcript(123)['text']=='[1.2] internal (7): Hello'
    def missing(*a): raise APIError('missing',404)
    client.get=missing
    assert client.transcript(123)['status']=='unavailable'
    def forbidden(*a): raise APIError('forbidden',403)
    client.get=forbidden
    assert client.transcript(123)['status']=='forbidden'

@pytest.mark.parametrize('company',['example',' EXAMPLE ','example.pipedrive.com','https://example.pipedrive.com/persons/list'])
def test_pipedrive_domain_and_token_normalization(company):
    client=Pipedrive(company,'  test-token  ')
    assert client.base=='https://example.pipedrive.com/api/'
    assert client.session.headers['x-api-token']=='test-token'

@pytest.mark.parametrize('company',['https://example.pipedrive.com.evil.test','https://evil.test/example.pipedrive.com','https://example.pipedrive.com@evil.test','http://example.pipedrive.com','example/pipedrive'])
def test_pipedrive_rejects_non_company_hosts(company):
    with pytest.raises(ValueError): Pipedrive(company,'test-token')

def test_pipedrive_auth_header_and_endpoint_versions(monkeypatch):
    import requests
    client=Pipedrive('example','test-token'); client.interval=0
    calls=[]
    def send(request,**kwargs):
        calls.append(request)
        response=requests.Response(); response.status_code=200
        response._content=b'{"success":true,"data":{"id":7}}' if request.url.endswith('users/me') else b'{"success":true,"data":[]}'
        return response
    monkeypatch.setattr(client.session,'send',send)
    assert client.current_user()['id']==7
    assert client.fields()==[]
    assert [r.url for r in calls]==['https://example.pipedrive.com/api/v1/users/me','https://example.pipedrive.com/api/v2/personFields?limit=500']
    assert all(r.headers['x-api-token']=='test-token' for r in calls)
    assert all('test-token' not in r.url for r in calls)

@pytest.mark.parametrize('status',[401,403])
def test_pipedrive_auth_errors_are_actionable_secret_free_and_not_retried(monkeypatch,status):
    import requests
    client=Pipedrive('example','test-token'); calls=[]
    def get(*args,**kwargs):
        calls.append(args)
        response=requests.Response(); response.status_code=status
        response._content=b'{"error":"test-token reflected by server"}'
        return response
    monkeypatch.setattr(client.session,'get',get)
    with pytest.raises(APIError) as error: client.fields()
    assert error.value.status==status
    assert 'test-token' not in str(error.value)
    assert ('Personal preferences' if status==401 else 'admin') in str(error.value)
    assert len(calls)==1

@pytest.mark.parametrize('token',['Bearer abc','abc\ndef','"abc"','abc def'])
def test_pipedrive_rejects_malformed_tokens(token):
    with pytest.raises(ValueError): Pipedrive('example',token)
