"""Synthetic agent calls and known Pipedrive response evidence for UI exploration."""
from datetime import timedelta
from demo import DemoAircallSearch,sample
from core import parse_dt
from service import build
from report_display import speed_to_lead


class DemoAgents(DemoAircallSearch):
    def calls(self,number,start,end):
        rows=super().calls(number,start,end)
        for i,c in enumerate(rows):
            c['user']={'id':101+i//2,'name':['Alex Demo','Jordan Demo','Casey Demo'][i//2]}
            c['direction']='outbound' if i<4 else 'inbound'
        return rows


def demo_response_report(dataset,settings):
    data=sample(); people=[]
    for i,c in enumerate(dataset['calls'],1):
        created=parse_dt(c['started_at'])-timedelta(minutes=3*i)
        people.append({'id':i,'name':c['name'],'add_time':created.isoformat(),'phones':[{'value':c['phone']}],
                       'label_ids':[1],'custom_fields':{'demo_source':'agent_demo','demo_campaign':'synthetic'}})
    if not people: return None
    data.update(people=people,start=min(p['add_time'] for p in people),end=dataset['meta']['end'],business_hours=settings.get('business_hours'))
    report=build(data,data['options']); report['speed_results']={}
    for p,c in zip(report['rows'],dataset['calls']):
        report['speed_results'][p['person_id']]=speed_to_lead({**p,'activity_start':data['start'],'activity_end':data['end'],'aircall_status':'complete'},
            [{'person_id':p['person_id'],'call_id':c['call_id'],'direction':c['direction'],'started_at':c['started_at']}])
    return report
