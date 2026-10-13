from copy import deepcopy
from datetime import datetime,timezone
from demo import sample,DemoAircall
from core import parse_dt
from service import build,enrich

def test_only_selected_numbers_transcribed_and_shared_numbers_skipped():
    data=sample();report=build(data,data['options'])
    report['rows'][1]['phones']=report['rows'][0]['phones'].copy()
    class Tracked(DemoAircall):
        def __init__(self): self.searched=[];self.transcribed=[]
        def calls(self,n,*args): self.searched.append(n);return super().calls(n,*args)
        def transcript(self,cid): self.transcribed.append(cid);return super().transcript(cid)
    client=Tracked()
    out=enrich(report,{},['1','2','4'],parse_dt(data['start']),datetime.now(timezone.utc),True,client=client)
    assert client.searched==['+12025550104']
    assert len(client.transcribed)==1
    assert len(out['issues'])==2
    assert {c['person_id'] for c in out['calls']}=={'4'}
    assert report['calls']==[]
