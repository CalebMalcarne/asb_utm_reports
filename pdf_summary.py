"""In-memory PDF summary used by full report downloads; no local client files."""
import io
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, LongTable, PageBreak
from core import UTMS, parse_dt, statistic_tags, summarize, summarize_all
from report_display import format_timestamp
from business_hours import validate_hours,schedule_label
from callback_metrics import callback_stats,duration_label,minute_range


def overall_summary_pdf(report, cohort, groups=None, created_at=None):
    created_at=created_at or datetime.now(timezone.utc)
    # Vera is bundled with ReportLab, so Streamlit Cloud needs no system fonts.
    font_dir=Path(reportlab.__file__).parent/'fonts'
    if 'ASBVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('ASBVera',str(font_dir/'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('ASBVeraBold',str(font_dir/'VeraBd.ttf')))
    navy=colors.HexColor('#18324f'); muted=colors.HexColor('#53677e'); line=colors.HexColor('#dce4ed')
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='ReportTitle',fontName='ASBVeraBold',fontSize=22,leading=28,textColor=navy,spaceAfter=12))
    styles.add(ParagraphStyle(name='SectionTitle',fontName='ASBVeraBold',fontSize=12,leading=17,textColor=navy,spaceBefore=16,spaceAfter=9,keepWithNext=True))
    styles.add(ParagraphStyle(name='Copy',fontName='ASBVera',fontSize=8,leading=12,textColor=navy,spaceAfter=5,splitLongWords=True))
    styles.add(ParagraphStyle(name='SmallCopy',parent=styles['Copy'],fontSize=7,leading=10,textColor=muted))
    styles.add(ParagraphStyle(name='TableHead',parent=styles['Copy'],fontName='ASBVeraBold',textColor=colors.white,fontSize=7,leading=10))

    def text(value):
        value=str(value if value is not None else '').replace('–','-').replace('—','-').replace('…','...')
        return escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',value)).replace('\n','<br/>')
    def p(value,style='Copy'): return Paragraph(text(value),styles[style])
    meta=report['meta']; opts=report['options']; catalog=report['catalog']
    zone=meta.get('timezone','UTC')
    def stamp(value):
        dt=parse_dt(value)
        return format_timestamp(value,zone) if dt else 'Not recorded'
    def labels(tokens): return ', '.join(f'{catalog.get(t,t)} [{t}]' for t in tokens) or 'None selected'
    stat_tags=statistic_tags(catalog,opts.get('stat_tags'))
    total=summarize_all(cohort,catalog,stat_tags)
    if total is None:
        total={'people':0,'matching_people':0,'match_pct':0,
               **{name+suffix:(0 if stat_tags[name] else None) for name in stat_tags for suffix in ('_people','_pct')}}
    qualified=set(stat_tags['qualified']); contacted=set(stat_tags['contacted'])
    featured=[r for r in cohort if r['matches_tag'] or qualified.intersection(r['tag_tokens'])]
    output=io.BytesIO(); width,height=landscape(A4); usable=width-72
    title=meta.get('name') or 'UTM performance report'
    doc=SimpleDocTemplate(output,pagesize=(width,height),rightMargin=36,leftMargin=36,topMargin=34,bottomMargin=36,
                          title=title,author='ASB Reports')
    story=[p('ASB / LEAD SOURCE PERFORMANCE','SmallCopy'),p(title,'ReportTitle')]
    identifying=[
        ('Export scope','All UTM codes' if groups is None else f'{len(groups)} selected UTM group(s)'),
        ('Person creation period',f"{stamp(meta.get('start'))} to {stamp(meta.get('end'))} (end exclusive)"),
        ('Report created / Pipedrive fetched',stamp(meta.get('fetched_at'))),
        ('PDF exported',stamp(created_at.isoformat())),
        ('Pipedrive company',meta.get('company') or 'Not recorded'),
        ('Report timezone',zone),
        ('Grouping fields',', '.join(opts.get('group_by',[]))),
        ('Searched tags / matching rule',labels(opts.get('tags',[]))+' / '+opts.get('tag_rule','Any selected tag')),
        ('Qualified statistic tags',labels(stat_tags['qualified'])),
        ('Contacted statistic tags',labels(stat_tags['contacted']))]
    if meta.get('demo'): identifying.insert(0,('Data type','SYNTHETIC DEMO - not live client data'))
    if meta.get('activity_start'):
        identifying.append(('Last Aircall fetch window',f"{stamp(meta['activity_start'])} to {stamp(meta['activity_end'])} (end exclusive)"))
        identifying.append(('Aircall fetched',stamp(meta.get('aircall_fetched_at'))))
    info=LongTable([[p(k,'SmallCopy'),p(v)] for k,v in identifying],colWidths=[170,usable-170],hAlign='LEFT',splitInRow=1)
    info.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
    story.extend([info,Spacer(1,12)])
    metric_rows=[['People in scope','Qualified','Matching searched tags','Contacted']]
    metric_rows.append([str(total['people'])]+[
        'Not configured' if total.get(name+'_pct') is None else f"{total[count]} / {total['people']} ({total[name+'_pct']:.2f}%)"
        for name,count in [('qualified','qualified_people'),('match','matching_people'),('contacted','contacted_people')]])
    metric=Table([[p(v,'TableHead' if i==0 else 'Copy') for v in row] for i,row in enumerate(metric_rows)],colWidths=[usable/4]*4)
    metric.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('BACKGROUND',(0,1),(-1,1),colors.HexColor('#edf3fa')),
                               ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.extend([metric,Spacer(1,8),p('Percentages use all people in the selected UTM scope. Qualified and matching categories may overlap; each person is counted once in each category. Contacted is based on configured Pipedrive tags, not inferred from call attempts.','SmallCopy'),
                  p('Tags reflect their values at the Pipedrive fetch time, not the date of qualification or conversion. Activity exports contain only calls already fetched. The last Aircall window can apply to only a subset of people; per-person windows are included in the people CSVs.','SmallCopy')])

    def tabular(headers,rows,widths):
        data=[[p(h,'TableHead') for h in headers]]+[[p(v,'SmallCopy') for v in row] for row in rows]
        # Keep ordinary person rows intact. Only allow an in-row split when a
        # single unusually long record cannot fit on a fresh page.
        oversized=any(max(cell.wrap(w-12,height)[1] for cell,w in zip(row,widths))>height-115 for row in data[1:])
        table=LongTable(data,colWidths=widths,repeatRows=1,hAlign='LEFT',splitByRow=1,splitInRow=int(oversized))
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f1f5f9')]),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,0),.5,line),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        return table
    story.append(p('UTM information','SectionTitle'))
    grouped=summarize(cohort,catalog,stat_tags)
    if grouped:
        def rate(row,key): return 'Not configured' if row[key] is None else f'{row[key]:.2f}%'
        story.append(tabular(['UTM group','People','Qualified','Matched','Contacted'],
                     [[g['utm_group'],g['people'],rate(g,'qualified_pct'),rate(g,'match_pct'),rate(g,'contacted_pct')] for g in grouped],
                     [usable-280,55,75,75,75]))
    else: story.append(p('No people in the selected UTM scope.'))
    story.extend([PageBreak(),p('Speed to Lead','ReportTitle')])
    hours=validate_hours(meta.get('business_hours'))
    story.extend([p('Business hours: '+schedule_label(hours)+' | '+hours['timezone']),
                  p('Workday leads: green at or under 5 minutes; yellow over 5 through 10 minutes; red over 10 minutes. After-hours leads: green at or under 24 elapsed hours from arrival; red over 24 hours. Nights and closed days count toward the 24-hour deadline.','SmallCopy'),
                  p('Response time means creation to first outbound Aircall attempt, answered or unanswered. These summaries include all people in this export scope. Pending, uncalled, and unknown results are excluded from callback averages; they are never treated as zero.','SmallCopy')])
    callbacks=callback_stats(cohort)
    callback_rows=[]
    for key,label in [('overall','Overall'),('workday','Workday leads'),('after_hours','Overnight / after-hours')]:
        stats=callbacks[key]; modes=stats['mode_minutes']
        mode='Not available' if not stats['count'] else 'No repeated range' if not modes else '\n'.join(minute_range(m) for m in modes[:5])
        if len(modes)>5: mode+=f'\n+{len(modes)-5} tied ranges'
        if modes: mode+=f"\n{stats['mode_count']} callbacks per range"
        callback_rows.append([label,stats['total'],stats['count'],stats['excluded'],
                              duration_label(stats['average_seconds']),duration_label(stats['shortest_seconds']),mode])
    story.append(tabular(['Lead category','Total leads','Verified callbacks','Excluded','Average callback','Shortest callback','Most frequent callback'],
                         callback_rows,[140,55,65,55,105,105,usable-525]))
    story.append(p('Most frequent callback uses one-minute ranges: 5-<6 min means at least 5 and less than 6 minutes. A range must contain at least two callbacks; all ties are counted, with up to five ranges shown.','SmallCopy'))
    late=sum(r.get('response_status')=='Late callback — over 24h' for r in cohort)
    overdue=sum(r.get('response_status')=='Overdue — no call as of lookup' for r in cohort)
    pending=sum(r.get('speed_to_lead_status')=='Loading…' for r in cohort)
    story.append(p(f'After-hours follow-up: {late} late callbacks; {overdue} overdue without a call as of lookup. Pending first-call lookups: {pending}.','SmallCopy'))
    checked=[parse_dt(r.get('first_call_checked_through')) for r in cohort]
    checked=[t for t in checked if t]
    if checked:
        story.append(p(f'Call-history check cutoffs range from {stamp(min(checked).isoformat())} to {stamp(max(checked).isoformat())}. Per-person cutoffs appear below.','SmallCopy'))
    story.append(p('Overdue status uses verified call history through the last lookup, not the PDF export time. Refresh Speed to Lead before exporting to include newer activity. Failed or incomplete lookups remain unknown.','SmallCopy'))
    story.append(p(f'Speed to Lead details - qualified or matching people ({len(featured)})','SectionTitle'))
    if not featured:
        story.append(p('No qualified or matching people in this scope. Summary metrics above still use all scoped leads.'))
    else:
        response_rows=[]
        for r in featured:
            after=r.get('after_hours_lead')
            category='After-hours' if after=='Yes' else 'Workday' if after=='No' else 'Unknown arrival'
            deadline=stamp(r['response_deadline']) if r.get('response_deadline') else 'Not applicable'
            status=r.get('response_status') or r.get('speed_to_lead_status') or 'Not fetched'
            color=r.get('speed_to_lead_color') or 'Unknown / pending'
            response_rows.append([f"{r['name']}\nPerson ID: {r['person_id']}\n{r['utm_group']}",
                                  f"{category}\nCreated: {stamp(r['created_at'])}\nFirst called: {stamp(r['first_called_at']) if r.get('first_called_at') else 'Not verified'}",
                                  r.get('speed_to_lead','Not fetched')+'\n'+color,
                                  ('24 elapsed hours' if after=='Yes' else '5 / 10 minutes' if after=='No' else 'Unknown')+'\nDeadline: '+deadline,
                                  status+'\nChecked through: '+stamp(r.get('first_call_checked_through'))])
        response_table=tabular(['Person / UTM group','Arrival / first call','Speed to Lead','Response target','Status / evidence cutoff'],
                               response_rows,[155,175,105,145,usable-580])
        fills={'Green':'#dcfce7','Yellow':'#fef9c3','Red':'#fee2e2'}
        for index,r in enumerate(featured,1):
            fill=fills.get(r.get('speed_to_lead_color'),'#e5e7eb')
            response_table.setStyle(TableStyle([('BACKGROUND',(2,index),(2,index),colors.HexColor(fill))]))
        story.append(response_table)
    story.append(PageBreak())
    story.append(p(f'Qualified or matching people ({len(featured)})','SectionTitle'))
    if not featured: story.append(p('No people are qualified or match the searched tags in this scope.'))
    else:
        rows=[]
        for r in featured:
            tags=set(r['tag_tokens'])
            flags=[label for condition,label in [(bool(tags & qualified),'Qualified'),(r['matches_tag'],'Matched'),(bool(tags & contacted),'Contacted')] if condition]
            rows.append([f"{r['name']}\nPerson ID: {r['person_id']}\nCreated: {stamp(r['created_at'])}",
                         ('\n'.join(r['phones']+r['emails']) or 'Not provided'),
                         '\n'.join(f'{u}: {r[u]}' for u in UTMS if r.get(u)),
                         '\n'.join(r['tags']) or 'None',', '.join(flags)])
        story.append(tabular(['Person','Contact details','UTM values','Pipedrive tags','Included as'],rows,[145,155,usable-480,100,80]))
    issues=[i for i in report.get('issues',[]) if str(i.get('person_id','')) in {r['person_id'] for r in cohort}]
    if issues:
        story.append(p('Activity matching notes','SectionTitle'))
        for i in issues: story.append(p(f"Person {i.get('person_id','')}: {i.get('issue','Review activity matching')}",'SmallCopy'))
    def footer(canvas,document):
        canvas.saveState(); canvas.setFont('ASBVera',7); canvas.setFillColor(muted)
        canvas.drawString(36,19,'ASB Reports | '+created_at.astimezone(ZoneInfo(zone)).strftime('%m/%d/%Y'))
        canvas.drawRightString(width-36,19,f'Page {document.page}'); canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()

