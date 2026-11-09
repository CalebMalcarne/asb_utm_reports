"""Agent analytics: independent call period, explicit Pipedrive response cohort."""
import copy
import hashlib
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from api import Aircall
from core import PRESETS,period,parse_dt,csv_bytes
from aircall_search import fetch_calls,fetch_transcript_batch
from agent_analytics import SORTS,agent_key,analyze_agents,report_parts,transcript_parts,safe_agent_folder
from agent_pdf import rank_label
from report_display import table_rows,format_timestamp
from service import zip_parts
from speed_lookup import SpeedLookup,lookup_key,pending_result
from business_hours import validate_hours


def _toggle(key): st.session_state[key]=not st.session_state.get(key,False)


def render_agent_analytics(settings,show_error):
    st.title('Agent Analytics')
    st.caption('All Aircall agents with calls in the selected period, including inbound-only agents and a separate unknown/system group. Calls made counts outbound attempts.')
    demo=st.checkbox('Use synthetic agent demo',False,key='agents_demo')
    from agent_demo import DemoAgents,demo_response_report
    def client(): return DemoAgents() if demo else Aircall(settings['aircall_id'],settings['aircall_token'])
    identity='demo' if demo else hashlib.sha256((settings.get('aircall_id','')+'\0'+settings.get('aircall_token','')).encode()).hexdigest()
    if st.session_state.get('agents_identity')!=identity:
        st.session_state.pop('agents_dataset',None); st.session_state.agents_identity=identity
    tz=settings.get('timezone','UTC'); now=datetime.now(timezone.utc); today=now.astimezone(ZoneInfo(tz)).date()
    preset=st.selectbox('Agent call period',PRESETS,index=3,key='agents_period')
    custom=None
    if preset=='Custom':
        a,b=st.columns(2)
        with a: begin=st.date_input('Agent calls from',today-timedelta(days=7),key='agents_begin')
        with b: finish=st.date_input('Agent calls through',today,key='agents_end')
        custom=(begin,finish)
    try: start,end,_,_=period(preset,tz,now,custom)
    except ValueError as exc: show_error(exc); return
    if st.button('Fetch agent calls',type='primary',key='agents_fetch'):
        try:
            with st.status('Loading all call activity…',expanded=True) as status:
                message=st.empty(); dataset=fetch_calls(settings,start,end,progress=message.write,client=client())
                dataset['meta']['demo']=demo; st.session_state.agents_dataset=dataset
                status.update(label='Agent calls loaded',state='complete')
        except Exception as exc: show_error(exc)
    dataset=st.session_state.get('agents_dataset')
    if not dataset: st.info('Fetch calls to view agents. Response averages use the Pipedrive UTM report loaded under Reports.'); return
    meta=dataset['meta']; stamp=lambda value:format_timestamp(value,meta['timezone'])
    st.caption(f"Loaded calls: {stamp(meta['start'])} to {stamp(meta['end'])} (end exclusive) · {meta['timezone']} · fetched {stamp(meta['fetched_at'])}")
    if parse_dt(meta['start'])!=start or abs((parse_dt(meta['end'])-end).total_seconds())>60 or meta['timezone']!=tz:
        st.warning('Date controls differ from the loaded calls. Fetch again to apply them; reports use the loaded period.')
    report=demo_response_report(dataset,settings) if demo else st.session_state.get('report')
    if report and bool(report['meta'].get('demo'))!=demo: report=None
    job=None; observed=None
    if report:
        report=copy.deepcopy(report); report['meta']['business_hours']=copy.deepcopy(validate_hours(settings.get('business_hours')))
        job=st.session_state.get('speed_lookup') if not demo else None
        if not demo:
            expected_key=lookup_key(report,settings)
            if job is None or st.session_state.get('speed_lookup_key')!=expected_key:
                if job: job.cancelled.set()
                job=SpeedLookup(report,settings)
                st.session_state.speed_lookup=job; st.session_state.speed_lookup_key=expected_key
            results,done=job.snapshot(); observed=(len(results),done)
            report['speed_results']={r['person_id']:results.get(r['person_id'],pending_result()) for r in report['rows']}
            st.caption(f"Lead response lookup: {len(results)} / {len(report['rows'])} checked. Calls without verified first-response evidence are excluded from averages.")
            if done and st.button('Refresh lead response times',key='agents_refresh_responses'):
                st.session_state.pop('speed_lookup_key',None); st.rerun()
        st.caption(f"Response cohort: {len(report['rows'])} Pipedrive UTM people created {stamp(report['meta']['start'])} to {stamp(report['meta']['end'])}. All tags are included.")
    else: st.info('Load a live Pipedrive UTM report under Reports to enable response averages. Calls outside that lead cohort still count toward activity totals.')
    sort_by=st.selectbox('Rank / sort agents by',list(SORTS),key='agents_sort')
    analysis=analyze_agents(dataset,report,sort_by)
    st.caption('Response averages credit only a verified first outbound call in this call period. Follow-ups, inbound calls, shared phones, and unknown first calls are excluded. After-hours responses use elapsed time from submission, with a 24-hour target. Calls are assigned to the Aircall user ID recorded on the call.')
    if not analysis['agents']: st.info('No calls found in this period.'); return
    st.dataframe(pd.DataFrame([{k:a[k] for k in ['agent','agent_id','calls_rank','calls_made','total_calls','response_rank','average_response','workday_response_rank','workday_average','workday_responses','after_hours_response_rank','after_hours_average','after_hours_responses']} for a in analysis['agents']]),hide_index=True,width='stretch')
    st.caption('Ties share rank (1, 1, 3). No response sample means unranked, not zero. Unknown/system calls have no agent rank. Average rankings show sample sizes and do not impose a minimum sample threshold.')
    def exports(key=None):
        prefix='agents_all' if key is None else safe_agent_folder(key)
        parts=report_parts(analysis,key)
        a,b=st.columns(2)
        with a: st.download_button('All-agent report ZIP' if key is None else 'Agent report ZIP',zip_parts(parts),prefix+'_report.zip','application/zip',key=prefix+'_report',on_click='ignore')
        with b: st.download_button('All-agent report PDF' if key is None else 'Agent report PDF',parts['Agent_Report.pdf'],prefix+'_report.pdf','application/pdf',key=prefix+'_pdf',on_click='ignore')
    def transcripts(key=None):
        prefix='agents_all' if key is None else safe_agent_folder(key)
        calls=[c for c in dataset['calls'] if key is None or agent_key(c)==key]
        pending=sum(c.get('transcript_status')=='not requested' for c in calls)
        available=sum(bool(str(c.get('transcript') or '').strip()) for c in calls)
        st.caption(f'Transcripts: {available} available; {pending} not requested; {len(calls)-pending-available} unavailable/empty/forbidden. Fetching all may take several minutes; completed batches are saved for resuming.')
        if st.button('Fetch all remaining transcripts',disabled=not pending,key=prefix+'_fetch_text'):
            try:
                with st.status('Fetching transcripts…',expanded=True) as status:
                    message=st.empty(); ac=client(); working=copy.deepcopy(dataset)
                    while True:
                        subset={'calls':[c for c in working['calls'] if key is None or agent_key(c)==key],'meta':working['meta']}
                        if not any(c.get('transcript_status')=='not requested' for c in subset['calls']): break
                        batch=fetch_transcript_batch(subset,settings,100,message.write,ac)
                        updates={c['call_id']:c for c in batch['calls']}
                        working['calls']=[updates.get(c['call_id'],c) for c in working['calls']]
                        working['meta']['transcripts_fetched_at']=batch['meta']['transcripts_fetched_at']
                        working['fetch_error']=batch.get('fetch_error')
                        st.session_state.agents_dataset=working
                        if batch.get('fetch_error'): break
                    status.update(label='Transcript retrieval finished',state='error' if working.get('fetch_error') else 'complete')
                st.rerun()
            except Exception as exc: show_error(exc)
        st.download_button('Download available transcripts ZIP',zip_parts(transcript_parts(analysis,key)),prefix+'_transcripts.zip','application/zip',disabled=not available,key=prefix+'_transcripts',on_click='ignore')
    exports()
    with st.expander('All-agent transcript downloads'): transcripts()
    if dataset.get('fetch_error'): st.error('Transcript retrieval stopped: '+dataset['fetch_error']+' Resume to retry remaining requests.')
    for agent in analysis['agents']:
        key=agent['agent_key']; prefix=safe_agent_folder(key); detail_key=prefix+'_expanded'
        with st.container(border=True):
            name,calls_col,work_col,after_col=st.columns([3,1,1.5,1.5])
            with name:
                st.button(('▾ ' if st.session_state.get(detail_key) else '▸ ')+agent['agent']+(' · ID '+agent['agent_id'] if agent['agent_id'] else ''),key=prefix+'_toggle',on_click=_toggle,args=(detail_key,),width='stretch')
                st.caption('Calls rank '+rank_label(agent,'calls_rank')+' · Response rank '+rank_label(agent,'response_rank'))
            calls_col.metric('Calls made',agent['calls_made'])
            work_col.metric('Workday avg',agent['workday_average'],help=f"{agent['workday_responses']} verified first responses")
            after_col.metric('After-hours avg',agent['after_hours_average'],help=f"{agent['after_hours_responses']} verified first responses")
            if st.session_state.get(detail_key):
                a,b,c,d=st.columns(4)
                a.metric('Total calls',agent['total_calls']); b.metric('Inbound calls',agent['calls_received'])
                c.metric('Call time',agent['total_call_time']); d.metric('Answered-to-end time',agent['total_answered_to_end'])
                st.caption(f"Answered: {agent['answered_calls']}; unanswered: {agent['unanswered_calls']}. Average call: {agent['average_call_time']}. Response samples: workday {agent['workday_responses']}, after-hours {agent['after_hours_responses']}. Answered-to-end time can include hold time.")
                calls=[c for c in analysis['calls'] if c['agent_key']==key]
                display=[{k:v for k,v in c.items() if k not in ('transcript','agent_key','aircall_tag_ids')} for c in calls]
                st.dataframe(pd.DataFrame(table_rows(display,meta['timezone'])),hide_index=True,width='stretch')
                st.download_button('Agent calls CSV',csv_bytes(display,zone=meta['timezone']),prefix+'_calls.csv','text/csv',key=prefix+'_calls',on_click='ignore')
                exports(key); transcripts(key)
    if job and observed and not observed[1]:
        job.start()
        @st.fragment(run_every=2)
        def watch_responses():
            current,done=job.snapshot()
            if (len(current),done)!=observed: st.rerun()
        watch_responses()
