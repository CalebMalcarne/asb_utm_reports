from datetime import datetime,timedelta,timezone
from core import period,auto_mapping

def sample():
    start,end,_,_=period('Last week','America/New_York')
    fields=[{'field_code':'label_ids','field_name':'Labels','field_type':'set','options':[{'id':1,'label':'Qualified'},{'id':2,'label':'Client'},{'id':3,'label':'Contacted'}]},
            {'field_code':'demo_source','field_name':'utm_source','field_type':'varchar'},
            {'field_code':'demo_campaign','field_name':'utm_campaign','field_type':'varchar'},
            {'field_code':'demo_tags','field_name':'Tags Aircall','field_type':'text'}]
    people=[]
    for i,(source,campaign,tags) in enumerate([('AFLT2421','Karmaoholic - 48824',[1]),('AFLT2421','Karmaoholic - 48824',[1,2]),
                       ('meta','retirement_october',[3]),('meta','retirement_october',[1]),('instagram','organic',[]),('','',[1])],1):
        people.append({'id':i,'name':f'Example Person {i}','add_time':(start+timedelta(hours=12*i)).isoformat(),
                       'phones':[{'value':f'+1202555010{i}'}],'emails':[{'value':f'example{i}@example.invalid'}],
                       'label_ids':tags,'custom_fields':{'demo_source':source,'demo_campaign':campaign,'demo_tags':'Qualified, Contacted' if 1 in tags else 'No Answer'}})
    options={'utm_fields':auto_mapping(fields),'tag_field':'label_ids','tags':['id:1'],'tag_rule':'Any selected tag','group_by':['utm_source','utm_campaign']}
    return {'fields':fields,'people':people,'start':start.isoformat(),'end':end.isoformat(),'fetched_at':datetime.now(timezone.utc).isoformat(),'demo':True,'options':options}

class DemoAircall:
    def calls(self,number,start,end):
        i=int(number[-1]); t=int(start)+86400
        if t>=end: return []
        return [{'id':900+i,'raw_digits':number,'started_at':t,'answered_at':t+5,'ended_at':t+125,'duration':125,
                 'direction':'outbound','user':{'name':'Example Rep'},'tags':[{'name':'Contacted'}],'comments':[]}]
    def transcript(self,call_id):
        return {'status':'available','text':'[0] internal: This is a synthetic example call.\n[5] external: Please send me the information.','raw':None}


class DemoAircallSearch(DemoAircall):
    """Independent demo so the original person-based example remains unchanged."""
    def tags(self):
        return [{'id':1,'name':'Qualified'}, {'id':2,'name':'Contacted'}, {'id':3,'name':'No Answer'}]

    def calls(self,number,start,end):
        rows=[]
        for i in range(1,6):
            t=int(start)+(int(end)-int(start))*i//6
            tags=[self.tags()[0],self.tags()[1]] if i<3 else ([self.tags()[1]] if i<5 else [])
            rows.append({'id':900+i,'raw_digits':f'+1202555010{i}','started_at':t,
                         'answered_at':t+5 if i<5 else None,'ended_at':t+125,'duration':125,
                         'direction':'inbound' if i%2 else 'outbound',
                         'contact':{'id':i,'first_name':'Demo','last_name':f'Contact {i}'},
                         'user':{'name':'Example Rep'},'tags':tags,'comments':[]})
        return rows

    def transcript(self,call_id):
        if str(call_id)=='905':
            return {'status':'unavailable','text':'','raw':None}
        text=('[0] internal: How can I help you?\n[5] external: I would like retirement protection. Please send me information.'
              if int(call_id)%2 else '[0] external: Please call me tomorrow about my account.')
        return {'status':'available','text':text,'raw':None}
