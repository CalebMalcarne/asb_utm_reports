import copy
import json
import time
from datetime import datetime,date,timedelta,time as walltime,timezone
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from api import APIError,Aircall,Pipedrive
from storage import Store
from core import (UTMS,PRESETS,period,parse_dt,field_code,field_name,auto_mapping,tag_catalog,
                  summarize,summarize_all,public_rows,csv_bytes,statistic_tags,group_tabs,group_filename)
from service import pd_client,fetch_people,build,enrich,select_rows,export_parts,zip_parts
from scheduler import next_run,validate_schedule
from mailer import make_payload,report_payload,send,DeliveryUnknown
from demo import sample,DemoAircall

UTC=timezone.utc
st.set_page_config(page_title='ASB UTM Reports',page_icon='📊',layout='wide')
store=Store(); settings=store.settings()
with st.sidebar:
    st.title('ASB Reports')
    page=st.radio('Menu',['Reports','Email & scheduling','Settings'])
    st.caption('Pipedrive → UTM groups → tags → Aircall')
    heartbeat=store.get('worker_heartbeat',0)
    st.caption('● Scheduler worker running' if time.time()-heartbeat<45 else '○ Scheduler worker offline')
    if st.button('Load demo',width='stretch'):
        st.session_state.data=sample(); st.session_state.pop('report',None); st.session_state.pop('options_key',None)
    if st.button('Clear report',width='stretch'):
        for k in ['data','report','options_key']: st.session_state.pop(k,None)
        st.rerun()


def show_error(exc):
    if isinstance(exc,(ValueError,APIError,DeliveryUnknown)): st.error(str(exc))
    else: st.error(f'{type(exc).__name__}: operation failed. No new result was accepted.')


def send_ui(payload,to,kind):
    jid=store.new_job(kind)
    store.finish(jid,'sending',f'{kind}; one SendGrid request in progress.')
    try:
        mid=send(store.settings(),payload)
        store.finish(jid,'accepted',f'SendGrid accepted {mid}. Recipient list: {to}')
        st.success('Accepted by SendGrid. Check the inbox or SendGrid Email Activity for delivery.')
    except DeliveryUnknown as exc:
        store.finish(jid,'uncertain',str(exc)); show_error(exc)
    except Exception as exc:
        store.finish(jid,'failed',str(exc) if isinstance(exc,ValueError) else type(exc).__name__); show_error(exc)


def report_scope(report,prefix,initial=None):
    rows=summarize(report['rows']); names={r['group_key']:r['utm_group'] for r in rows}
    for key in initial or []: names.setdefault(key,'Saved group (absent from current report): '+key)
    mode=st.radio('UTM groups',['All UTM groups','Choose UTM groups'],index=0 if initial is None else 1,horizontal=True,key=prefix+'_mode')
    chosen=None if mode=='All UTM groups' else st.multiselect('Include groups',list(names),default=initial or [],format_func=lambda k:names[k],key=prefix+'_groups')
    return chosen

if page=='Settings':
    st.title('Settings')
    st.caption('API credentials and defaults are saved on this computer. Nothing is sent until you fetch data or use an email action.')
    with st.form('connections'):
        a,b=st.columns(2)
        with a:
            st.subheader('Pipedrive')
            company=st.text_input('Company subdomain',settings['company'],placeholder='yourcompany',help='Company subdomain or https://yourcompany.pipedrive.com URL; use the same company as your personal API token.')
            pd_key=st.text_input('Pipedrive API token',settings['pipedrive_token'],type='password',help='Pipedrive → Personal preferences → API. Paste the personal token only, not an OAuth client secret.')
            st.subheader('Aircall')
            ac_id=st.text_input('Aircall API ID',settings['aircall_id'],type='password')
            ac_key=st.text_input('Aircall API token',settings['aircall_token'],type='password')
        with b:
            st.subheader('Twilio SendGrid')
            sg_key=st.text_input('SendGrid API key',settings['sendgrid_key'],type='password')
            sender=st.text_input('Verified sender email',settings['sender_email'])
            sender_name=st.text_input('Sender name',settings['sender_name'])
            region=st.selectbox('SendGrid data region',['US','EU'],index=['US','EU'].index(settings['sendgrid_region']))
            zones=['America/New_York','America/Chicago','America/Denver','America/Los_Angeles','UTC']
            zone=st.selectbox('Report timezone',zones,index=zones.index(settings['timezone']) if settings['timezone'] in zones else 0)
        save=st.form_submit_button('Save API settings')
        connect=st.form_submit_button('Save & connect Pipedrive / refresh fields',type='primary')
        if save or connect:
            try:
                company=Pipedrive.normalize_company(company) if company.strip() else ''
            except ValueError as exc:
                show_error(exc); st.stop()
            changed=company.strip()!=settings['company'] or pd_key.strip()!=settings['pipedrive_token']
            settings.update(company=company.strip(),pipedrive_token=pd_key.strip(),aircall_id=ac_id.strip(),aircall_token=ac_key.strip(),
                            sendgrid_key=sg_key.strip(),sender_email=sender.strip(),sender_name=sender_name.strip(),sendgrid_region=region,timezone=zone)
            if changed:
                settings['utm_fields']={}; store.put('fields',[])
                for k in ['data','report']: st.session_state.pop(k,None)
                for schedule in store.schedules(): store.enable(schedule['id'],False)
            store.save_settings(settings); st.success('Settings saved. If the Pipedrive connection changed, schedules were paused; refresh the field mapping before enabling them.')
    st.caption('Credentials are encrypted locally. The encryption key is kept in the same .data folder, so protect that folder with your OS account permissions. It is excluded from Git.')
    if connect:
        try:
            client=pd_client(store.settings())
            account=client.current_user()
            st.success(f'Credentials accepted for {client.company}.pipedrive.com (user ID {account["id"]}).')
            fields=client.fields(); store.put('fields',fields)
            current=store.settings()
            inferred=auto_mapping(fields)
            current['utm_fields']={u:current.get('utm_fields',{}).get(u) or inferred.get(u,'') for u in UTMS}
            store.save_settings(current); settings=current
            st.success(f'Connected. Loaded {len(fields)} person-field definitions.')
        except Exception as exc: show_error(exc)
    if st.button('Test Aircall connection'):
        try:
            s=store.settings(); Aircall(s['aircall_id'],s['aircall_token']).get('tags',{'per_page':1})
            st.success('Aircall connection succeeded.')
        except Exception as exc: show_error(exc)
    fields=store.get('fields',[])
    if fields:
        st.subheader('Pipedrive field mapping')
        names={field_code(f):f'{field_name(f)} [{field_code(f)}]' for f in fields if field_code(f)}
        with st.form('mapping'):
            mapped={}
            cols=st.columns(2)
            for i,u in enumerate(UTMS):
                choices=['']+list(names); default=settings.get('utm_fields',{}).get(u,'')
                with cols[i%2]: mapped[u]=st.selectbox(u,choices,index=choices.index(default) if default in choices else 0,format_func=lambda k:names.get(k,'Not mapped'))
            tag_options=['label_ids']+[k for k in names if k!='label_ids']
            default=settings.get('tag_field','label_ids')
            tag_field=st.selectbox('Person tag field',tag_options,index=tag_options.index(default) if default in tag_options else 0,
                format_func=lambda k:'Native person labels' if k=='label_ids' else names[k])
            st.caption('Choose native labels, a multi-option field, or a comma-separated text field such as Tags Aircall. Two similarly named Aircall fields are kept separate.')
            if st.form_submit_button('Save field mapping'):
                s=store.settings(); s.update(utm_fields=mapped,tag_field=tag_field); store.save_settings(s)
                st.success('Mapping saved. Fetch a new report to use it. Existing schedules keep their saved report rules.')
        tags=tag_catalog(fields,[],tag_field)
        if tags: st.write('Configured tag choices: '+', '.join(tags.values()))
        else: st.caption('For text tag fields, available choices are discovered from the people fetched for the report.')
    st.stop()

if page=='Reports':
    st.title('UTM performance')
    st.caption('People created in Pipedrive during your selected period, with at least one mapped UTM value. Tags reflect their current state.')
    cols=st.columns([2,2,2])
    with cols[0]: preset=st.selectbox('Person creation period',PRESETS,index=3)
    now=datetime.now(UTC); tz=settings['timezone']
    if preset=='Custom':
        with cols[1]: begin=st.date_input('From',now.astimezone(ZoneInfo(tz)).date()-timedelta(days=7))
        with cols[2]: finish=st.date_input('Through (inclusive)',now.astimezone(ZoneInfo(tz)).date())
        custom=(begin,finish)
    else: custom=None
    try:
        start,end,a,b=period(preset,tz,now,custom)
        st.caption(f'{a} through {b} · {tz} · current-day data stops at the fetch time')
    except ValueError as exc: show_error(exc); st.stop()
    if st.button('Fetch Pipedrive people',type='primary'):
        options={'utm_fields':settings.get('utm_fields',{}),'tag_field':settings.get('tag_field','label_ids'),
                 'tags':[],'tag_rule':'Any selected tag','group_by':['utm_source','utm_campaign']}
        with st.status('Fetching people…',expanded=True) as status:
            message=st.empty()
            try:
                data=fetch_people(settings,options,start,end,message.write)
                st.session_state.data=data; st.session_state.pop('report',None); st.session_state.pop('options_key',None)
                status.update(label='Pipedrive import complete',state='complete')
            except Exception as exc: status.update(label='Import failed',state='error'); show_error(exc)
    data=st.session_state.get('data')
    if not data:
        st.info('Set up Pipedrive in Settings, or click Load demo to explore the interface. GHL is no longer required.')
        st.stop()
    if data['demo']: st.info('Synthetic demo data. No live client records or calls are used. Emailing this report and scheduling from it are disabled.')
    st.caption(f'Loaded period: {data["start"]} → {data["end"]} (exclusive) · fetched {data["fetched_at"]}')
    if not data['demo'] and (parse_dt(data['start'])!=start or abs((parse_dt(data['end'])-end).total_seconds())>60):
        st.warning('Date controls differ from the loaded report. Click Fetch Pipedrive people to replace it; exports below use the displayed loaded period.')
    catalog=tag_catalog(data['fields'],data['people'],data['options']['tag_field'])
    a,b,c=st.columns([2,1,2])
    dataset_key=data['fetched_at']
    with a:
        selected=st.multiselect('Report tags',list(catalog),default=[k for k,v in catalog.items() if v.casefold()=='qualified'],format_func=lambda k:catalog[k],key='tags_'+dataset_key)
    with b: rule=st.selectbox('Match rule',['Any selected tag','All selected tags'])
    with c:
        grouping=st.selectbox('Group by',['Source + campaign','utm_source','utm_campaign','utm_medium','utm_content','utm_term'])
    with st.expander('Qualified / contacted statistic tags'):
        st.caption('Each statistic counts a person once if they have any of its selected tags. Defaults use the exact Qualified and Contacted tags; an unconfigured statistic is shown as —.')
        stat_tags={}
        defaults=statistic_tags(catalog,data['options'].get('stat_tags'))
        for name in ('qualified','contacted'):
            stat_tags[name]=st.multiselect(name.title()+' tags',list(catalog),default=[t for t in defaults[name] if t in catalog],format_func=lambda k:catalog[k],key='stat_'+name+'_'+dataset_key)
    data['options']['stat_tags']=copy.deepcopy(stat_tags)
    options={**data['options'],'tags':selected,'tag_rule':rule,'stat_tags':stat_tags,'group_by':['utm_source','utm_campaign'] if grouping=='Source + campaign' else [grouping]}
    if not selected: st.info('Select a tag to identify qualified/client people. Group totals still show all UTM people.')
    fingerprint=json.dumps(options,sort_keys=True)+data['fetched_at']
    if st.session_state.get('options_key')!=fingerprint:
        try:
            fresh=build(data,options)
            old=st.session_state.get('report')
            if old and old['meta']['fetched_at']==fresh['meta']['fetched_at']:
                old_rows={r['person_id']:r for r in old['rows']}
                new_rows={r['person_id']:r for r in fresh['rows']}
                for r in fresh['rows']:
                    old_r=old_rows.get(r['person_id'],{})
                    for k in ['call_count','answered_calls','transcripts_available','aircall_status']:
                        if k in old_r: r[k]=old_r[k]
                for call in old['calls']:
                    if call['person_id'] in new_rows:
                        r=new_rows[call['person_id']]; fresh['calls'].append({**call,'group_key':r['group_key'],'utm_group':r['utm_group']})
                fresh['issues']=old['issues']
                fresh['meta'].update({k:v for k,v in old['meta'].items() if k.startswith('activity_') or k=='aircall_fetched_at'})
            st.session_state.report=fresh; st.session_state.options_key=fingerprint
        except Exception as exc: show_error(exc); st.stop()
    report=st.session_state.report
    summary=summarize(report['rows'],report['catalog'],report['options'].get('stat_tags'))
    st.caption(f"{len(report['rows']):,} UTM people across {len(summary):,} groups. Select a tab to view and export that group.")
    if not summary:
        st.info('No UTM people in this reporting period.'); st.stop()
    tab_groups=[('All UTM codes',None)]
    used_labels={'All UTM codes'}
    for label,group_key in group_tabs(summary).items():
        while label in used_labels: label+=' (UTM group)'
        used_labels.add(label); tab_groups.append((label,group_key))
    tab_key='utm_tabs_'+json.dumps(options['group_by'])+'_'+data['fetched_at']
    # Render each scope once; tab clicks only switch visibility in the browser.
    tabs=st.tabs([label for label,_ in tab_groups],key=tab_key,on_change='ignore')
    for tab,(label,group_key) in zip(tabs,tab_groups):
        with tab:
            groups=None if group_key is None else [group_key]
            scope='all' if group_key is None else 'group_'+group_key
            filtered=select_rows(report,groups)
            stat=summarize_all(filtered,report['catalog'],report['options'].get('stat_tags')) if groups is None else next(r for r in summary if r['group_key']==group_key)
            filename='all_utm_codes' if groups is None else group_filename(group_key)
            st.caption(stat['utm_group'])
            a,b,c,d=st.columns(4)
            a.metric('People across all UTM codes' if groups is None else 'People in this UTM group',stat['people'])
            b.metric('Matching selected tags',stat['matching_people'],delta=f"{stat['match_pct']:.1f}% of group",delta_color='off')
            for col,name in ((c,'qualified'),(d,'contacted')):
                pct=stat[name+'_pct']; count=stat[name+'_people']
                col.metric(name.title(),f'{pct:.1f}%' if pct is not None else '—',
                           delta=f"{count} of {stat['people']} people" if count is not None else 'Select statistic tags',delta_color='off')
            st.caption('Qualified and contacted are based on current Pipedrive tags. Each percentage uses everyone in this tab, including people hidden by the matching-only filter.')
            if groups is None:
                st.subheader('UTM group comparison')
                st.dataframe(pd.DataFrame([{k:v for k,v in r.items() if k!='group_key'} for r in summary]),hide_index=True,width='stretch')
            only=st.checkbox('Show/export only people matching the selected tags',False,key='only_'+scope)
            detail=select_rows(report,groups,only)
            st.subheader('Data sheet')
            st.dataframe(pd.DataFrame(public_rows(detail)),hide_index=True,width='stretch')
            if not detail: st.info('No people match this detail filter. Group statistics still use all people in this tab.')
            parts=export_parts(report,groups,only)
            x,y,z=st.columns(3)
            with x: st.download_button('Summary CSV',parts['utm_summary.csv'],filename+'_summary.csv','text/csv',key='summary_'+scope,on_click='ignore')
            with y: st.download_button('People CSV',parts['people.csv'],filename+'_people.csv','text/csv',key='people_'+scope,on_click='ignore')
            with z: st.download_button('Full report ZIP',zip_parts(parts),filename+'_report.zip','application/zip',key='zip_'+scope,on_click='ignore')
            st.caption('Downloads include all UTM codes. Summary CSV includes a total row and a row per group; use row_type to distinguish them.' if groups is None else 'These downloads contain only this UTM group. Both CSVs include qualified/contacted counts and percentages; the ZIP also includes any fetched activity and transcripts for this group.')
            st.subheader('Aircall activity & transcripts')
            target_rows=select_rows(report,groups)
            names={r['person_id']:f'{r["name"]} · person {r["person_id"]}' for r in target_rows}
            ids=st.multiselect('People to retrieve',list(names),default=[r['person_id'] for r in target_rows if r['matches_tag']],
                                format_func=lambda k:names[k],key='aircall_people_'+fingerprint+str(groups))
            c1,c2,c3=st.columns(3)
            with c1: activity_begin=st.date_input('Activity from',parse_dt(data['start']).astimezone(ZoneInfo(tz)).date(),key='activity_begin_'+scope)
            with c2: activity_finish=st.date_input('Activity through',datetime.now(ZoneInfo(tz)).date(),key='activity_finish_'+scope)
            with c3: transcripts=st.checkbox('Fetch available transcripts',True,key='transcripts_'+scope)
            if st.button('Fetch calls & transcripts',disabled=not ids,key='fetch_'+scope):
                try:
                    ac_start,ac_end,_,_=period('Custom',tz,custom=(activity_begin,activity_finish))
                    with st.status('Retrieving selected phone numbers…',expanded=True) as status:
                        message=st.empty()
                        st.session_state.report=enrich(report,settings,ids,ac_start,ac_end,transcripts,message.write,DemoAircall() if data['demo'] else None)
                        status.update(label='Aircall retrieval complete',state='complete')
                    st.rerun()
                except Exception as exc: show_error(exc)
            if report['calls']:
                display_ids={r['person_id'] for r in filtered}
                calls=[c for c in report['calls'] if c['person_id'] in display_ids]
                st.dataframe(pd.DataFrame([{k:v for k,v in c.items() if k not in ('transcript','group_key')} for c in calls]),hide_index=True,width='stretch')
                if calls:
                    chosen=st.selectbox('Read a transcript',[c['call_id'] for c in calls],key='read_'+scope)
                    call=next(c for c in calls if c['call_id']==chosen)
                    st.text_area('Transcript',call['transcript'] or f'Transcript: {call["transcript_status"]}',height=220,disabled=True,key='transcript_text_'+scope)
                    st.download_button('Aircall activity CSV',csv_bytes([{k:v for k,v in c.items() if k!='group_key'} for c in calls]),filename+'_aircall_activity.csv','text/csv',key='activity_csv_'+scope,on_click='ignore')
            if report['issues']:
                with st.expander('Matching issues'): st.dataframe(pd.DataFrame([issue for issue in report['issues'] if str(issue.get('person_id','')) in {r['person_id'] for r in filtered}]),hide_index=True)
            st.caption('All call directions and durations are included. Transcript availability depends on Aircall. Shared numbers within this cohort are flagged rather than assigned to multiple people.')
    st.stop()

# Email & scheduling
st.title('Email & scheduling')
test,current,schedules,history=st.tabs(['Test email','Send current report','Schedules','Send history'])
with test:
    st.write('Send a simple connection test to one specified address. It contains no client data.')
    to=st.text_input('Test email address',placeholder='you@yourcompany.com')
    if st.button('Send test email',type='primary'):
        try:
            from mailer import recipients
            if len(recipients(to))!=1: raise ValueError('Use one address for a test email.')
            payload=make_payload(settings,to,'ASB Reports — SendGrid test','This is the ASB Reports email connection test. No client data is included.')
            send_ui(payload,to,'Connection test')
        except Exception as exc: show_error(exc)
with current:
    report=st.session_state.get('report')
    if not report: st.info('Build a report first in Reports.')
    else:
        groups=report_scope(report,'mail')
        to=st.text_area('Report recipient emails',placeholder='manager@yourcompany.com')
        only=st.checkbox('Include only matching people in detail attachments',False,key='mail_only')
        details=st.checkbox('Attach individual people CSV',False,key='mail_details')
        calls=st.checkbox('Attach fetched Aircall activity CSV',False,key='mail_calls')
        transcripts=st.checkbox('Include transcript text in the activity CSV',False,key='mail_transcripts',disabled=not calls)
        st.caption('UTM summary CSV is always attached. Each recipient receives their own copy. This sends the loaded report; use a schedule for fresh automatic reports.')
        allowed=bool(select_rows(report,groups)) and not report['meta']['demo']
        if st.button('Send current report now',disabled=not allowed):
            try:
                payload=report_payload(settings,to,report,groups,only,details,calls,transcripts and calls)
                send_ui(payload,to,'Current UTM report')
            except Exception as exc: show_error(exc)
        if report['meta']['demo']: st.caption('Live report sends are disabled for demo data.')
with schedules:
    st.caption('Schedules persist across restarts. The worker must be running on an awake computer. Paused schedules do not send. After downtime, one fresh catch-up report is sent; older missed runs are skipped.')
    existing=store.schedules()
    if existing:
        for s in existing:
            with st.expander(s['name']+(' · enabled' if s['enabled'] else ' · paused')):
                st.write(f"Next: {datetime.fromtimestamp(s['next_run'],ZoneInfo(s['spec']['timezone'])).isoformat()}")
                st.write(f"{s['spec']['frequency']} · report {s['spec']['period']} · recipients: {s['spec']['recipients']}")
                st.json(s['spec'],expanded=False)
                a,b=st.columns(2)
                with a:
                    if st.button('Pause' if s['enabled'] else 'Enable',key='toggle_'+s['id']):
                        try:
                            if not s['enabled']: validate_schedule(s['spec'],settings)
                            store.enable(s['id'],not s['enabled'],next_run(s['spec'],time.time()) if not s['enabled'] else None); st.rerun()
                        except Exception as exc: show_error(exc)
                with b:
                    if st.button('Run once now',key='now_'+s['id'],disabled=not s['enabled']):
                        store.enable(s['id'],True,time.time()); st.success('Queued for the worker. It will fetch fresh data and send to the saved recipients.')
    report=st.session_state.get('report')
    if not report or report['meta']['demo']:
        st.info('Load a live report and select its tags to create or replace a schedule. Saved schedules above can still be managed.')
    else:
        st.subheader('Create or replace a schedule')
        editing=st.selectbox('Schedule',['New schedule']+[s['id'] for s in existing],format_func=lambda sid:next((s['name'] for s in existing if s['id']==sid),sid))
        selected_schedule=next((s for s in existing if s['id']==editing),None)
        saved_spec=selected_schedule['spec'] if selected_schedule else {}
        st.caption('Saving uses the current report’s UTM mapping, tag field, tag selection, qualified/contacted statistic tags, and grouping. Date ranges below are relative and recalculated each run.')
        name=st.text_input('Schedule name',selected_schedule['name'] if selected_schedule else 'Weekly UTM report',key='schedule_name_'+editing)
        to=st.text_area('Scheduled recipient emails',saved_spec.get('recipients',''),key='schedule_to_'+editing)
        modes=['Daily','Weekly','Monthly','Every N hours']
        frequency=st.selectbox('Send frequency',modes,index=modes.index(saved_spec.get('frequency','Weekly')),key='freq_'+editing)
        sched_zones=['America/New_York','America/Chicago','America/Denver','America/Los_Angeles','UTC']
        tz=st.selectbox('Schedule timezone',sched_zones,index=sched_zones.index(saved_spec.get('timezone',settings['timezone'])),key='sched_tz_'+editing)
        at=st.time_input('Local send time',walltime.fromisoformat(saved_spec.get('at','09:00')),key='at_'+editing)
        weekday=st.selectbox('Weekday',list(range(7)),format_func=lambda d:['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][d],index=int(saved_spec.get('weekday',0)),disabled=frequency!='Weekly',key='weekday_'+editing)
        monthday=st.number_input('Day of month (clamped to month end)',1,31,int(saved_spec.get('monthday',1)),disabled=frequency!='Monthly',key='monthday_'+editing)
        hours=st.number_input('Interval hours',1,8760,int(saved_spec.get('hours',168)),disabled=frequency!='Every N hours',key='hours_'+editing)
        periods=['Yesterday','Last week','Last month','Previous N complete days']
        report_period=st.selectbox('Report window',periods,index=periods.index(saved_spec.get('period','Last week')),key='period_'+editing)
        days=st.number_input('Number of complete days',1,365,int(saved_spec.get('days',7)),disabled=report_period!='Previous N complete days',key='days_'+editing)
        groups=report_scope(report,'schedule_'+editing,saved_spec.get('groups'))
        only=st.checkbox('Matching people only in detail attachments',saved_spec.get('matched_only',False),key='sched_only_'+editing)
        details=st.checkbox('Attach people CSV',saved_spec.get('include_details',False),key='sched_details_'+editing)
        calls=st.checkbox('Fetch and attach Aircall activity for matching people',saved_spec.get('include_calls',False),key='sched_calls_'+editing)
        transcripts=st.checkbox('Fetch and include transcripts in activity CSV',saved_spec.get('include_transcripts',False),disabled=not calls,key='sched_transcripts_'+editing)
        followup_modes=['Report period only','Through send time']
        followup=st.selectbox('Aircall follow-up',followup_modes,index=followup_modes.index(saved_spec.get('followup','Report period only')),key='followup_'+editing)
        enabled=st.checkbox('Enable automatic sending',bool(selected_schedule and selected_schedule['enabled']),key='sched_enabled_'+editing)
        st.caption('Email includes the summary plus only the optional attachments selected above. Calls/transcripts are fetched only for people matching the saved tags.')
        spec={'frequency':frequency,'timezone':tz,'at':at.strftime('%H:%M'),'weekday':weekday,'monthday':int(monthday),'hours':int(hours),
              'anchor':saved_spec.get('anchor',time.time()),'period':report_period,'days':int(days),'recipients':to,
              'options':copy.deepcopy(report['options']),'groups':groups,'matched_only':only,'include_details':details,
              'include_calls':calls,'include_transcripts':transcripts and calls,'followup':followup}
        st.write('Next scheduled run: '+datetime.fromtimestamp(next_run(spec,time.time()),ZoneInfo(tz)).isoformat())
        if st.button('Save schedule',type='primary'):
            try:
                if not name.strip(): raise ValueError('Enter a schedule name.')
                validate_schedule(spec,settings)
                store.save_schedule(name.strip(),spec,next_run(spec,time.time()),enabled,None if editing=='New schedule' else editing)
                st.success('Schedule saved.'); st.rerun()
            except Exception as exc: show_error(exc)
with history:
    jobs=store.jobs()
    if jobs:
        for j in jobs:
            j['created']=datetime.fromtimestamp(j['created'],UTC).isoformat(); j['updated']=datetime.fromtimestamp(j['updated'],UTC).isoformat()
        st.dataframe(pd.DataFrame(jobs),hide_index=True,width='stretch')
    else: st.info('No email jobs yet.')
    st.caption('Accepted = SendGrid accepted the request, not confirmed inbox delivery. Uncertain or stale sending/preparing jobs are not retried automatically. Check SendGrid activity before resending.')

