"""Portable PDF rankings for all agents or one agent, using the same analytics."""
import io
import re
from pathlib import Path
from datetime import datetime,timezone
from xml.sax.saxutils import escape
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4,landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate,Paragraph,LongTable,TableStyle,Spacer
from report_display import format_timestamp
from business_hours import validate_hours,schedule_label


def rank_label(agent,field):
    return f"#{agent[field]} of {agent[field+'_of']}" if agent[field] is not None else 'Unranked'


def agent_report_pdf(analysis,key=None):
    rows=[a for a in analysis['agents'] if key is None or a['agent_key']==key]
    if key is not None and not rows: raise ValueError('Unknown agent.')
    font_dir=Path(reportlab.__file__).parent/'fonts'
    if 'AgentVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('AgentVera',str(font_dir/'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('AgentVeraBold',str(font_dir/'VeraBd.ttf')))
    navy=colors.HexColor('#18324f'); width,height=landscape(A4); usable=width-72
    styles={k:ParagraphStyle(k,fontName='AgentVeraBold' if k in ('title','head','section') else 'AgentVera',
               fontSize=size,leading=size+3,textColor=colors.white if k=='head' else navy,
               spaceAfter=8 if k in ('title','section') else 3,keepWithNext=k in ('title','section'))
            for k,size in [('title',20),('section',12),('body',8),('small',7),('head',7)]}
    def p(value,style='body'):
        clean=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value)).replace('—','-').replace('–','-').replace('…','...')
        return Paragraph(escape(clean).replace('\n','<br/>'),styles[style])
    def table(headers,data,widths):
        cells=[[p(h,'head') for h in headers]]+[[p(v,'small') for v in r] for r in data]
        oversized=any(max(cell.wrap(w-12,height)[1] for cell,w in zip(row,widths))>height-115 for row in cells[1:])
        t=LongTable(cells,colWidths=widths,repeatRows=1,splitByRow=1,splitInRow=int(oversized),hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#edf3fa')]),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        return t
    meta=analysis['meta']; zone=meta['timezone']; now=datetime.now(timezone.utc)
    stamp=lambda value:format_timestamp(value,zone)
    title='All agent report' if key is None else rows[0]['agent']+' - agent report'
    story=[p('ASB / AGENT ANALYTICS','small'),p(title,'title'),
           p(f"Calls: {stamp(meta['start'])} to {stamp(meta['end'])} (end exclusive) | {zone}"),
           p(f"Calls fetched: {stamp(meta['fetched_at'])} | Report created: {stamp(now)}",'small'),
           p('Sorted by: '+meta['sort_by']+' | Calls made means outbound attempts, including unanswered calls.','small')]
    if meta.get('demo'): story.append(p('SYNTHETIC DEMO - no live client records','small'))
    source=meta.get('response_source')
    if source:
        story.append(p(f"Response cohort: {meta['response_cohort_people']} loaded Pipedrive UTM people, created {stamp(source['start'])} to {stamp(source['end'])} (end exclusive).",'small'))
        story.append(p('Business hours: '+schedule_label(validate_hours(source.get('business_hours')))+' | '+validate_hours(source.get('business_hours'))['timezone'],'small'))
    else: story.append(p('No Pipedrive response cohort linked. Call metrics are available; response averages and response ranks are unavailable.','small'))
    story.extend([p(meta['response_rules'],'small'),p(meta['ranking_rules'],'small'),
                  p('Workday target: green <=5 minutes; yellow >5 through 10 minutes; red >10 minutes. After-hours target: 24 elapsed hours from creation, including nights and closed days.','small'),
                  p('Agent credit uses the Aircall user ID on the call; this does not infer every participant in a transfer or conference. '+meta['duration_rules'],'small')])
    story.append(p('Agent rankings' if key is None else 'Ranking within all agents in this period','section'))
    story.append(table(['Agent / ID','Calls rank','Calls made / total','Overall response rank','Workday average / samples','After-hours average / samples','Call time / answered-to-end'],
        [[a['agent']+'\nID: '+(a['agent_id'] or 'Not available'),rank_label(a,'calls_rank'),f"{a['calls_made']} / {a['total_calls']}",
          rank_label(a,'response_rank')+'\n'+a['average_response'],f"{a['workday_average']}\nn={a['workday_responses']}",
          f"{a['after_hours_average']}\nn={a['after_hours_responses']}",a['total_call_time']+'\n'+a['total_answered_to_end']] for a in rows],
        [150,65,65,110,110,110,usable-610]))
    story.append(Spacer(1,12))
    story.append(p('Activity and response detail','section'))
    story.append(table(['Agent','Outbound / inbound','Answered / unanswered','Average call / answered-to-end','Workday response rank','After-hours response rank','Transcripts ready / pending'],
        [[a['agent'],f"{a['calls_made']} / {a['calls_received']}",f"{a['answered_calls']} / {a['unanswered_calls']}",a['average_call_time']+'\n'+a['average_answered_to_end'],
          rank_label(a,'workday_response_rank'),rank_label(a,'after_hours_response_rank'),f"{a['available_transcripts']} / {a['pending_transcripts']}"] for a in rows],
        [150,75,80,130,90,90,usable-615]))
    if key is not None:
        calls=[c for c in analysis['calls'] if c['agent_key']==key]
        story.extend([Spacer(1,12),p('Call data sheet','section'),
            table(['Call / contact','Started / direction','Duration / answered-to-end','Pipedrive person / submission','First response / inclusion'],
                  [[c['call_id']+'\n'+c['name'],stamp(c['started_at'])+'\n'+c['direction'],c['call_duration']+'\n'+c['answered_to_end'],
                    (c['person_id'] or 'Not matched')+'\n'+c['lead_submission_type'],c['response_time']+'\n'+c['response_match']] for c in calls],
                  [160,145,120,135,usable-560])])
    def footer(canvas,doc):
        canvas.setFont('AgentVera',7); canvas.setFillColor(navy)
        canvas.drawString(36,20,'ASB Reports | '+stamp(now))
        canvas.drawRightString(width-36,20,f'Page {doc.page}')
    out=io.BytesIO()
    SimpleDocTemplate(out,pagesize=(width,height),leftMargin=36,rightMargin=36,topMargin=32,bottomMargin=36,title=title).build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
