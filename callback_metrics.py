"""Callback summaries use each person's verified first outbound attempt once."""
from collections import Counter
import math
from statistics import fmean

from core import parse_dt


def duration_label(seconds):
    if seconds is None: return '—'
    hours,rest=divmod(int(seconds+0.5),3600)
    minutes,seconds=divmod(rest,60)
    return f'{hours}h {minutes:02d}m {seconds:02d}s'


def minute_range(minute):
    return f'{minute}–<{minute+1} min'


def _summary(values,total):
    frequencies=Counter(int(value//60) for value in values)
    highest=max(frequencies.values(),default=0)
    modes=sorted(minute for minute,count in frequencies.items() if count==highest) if highest>1 else []
    return dict(total=total,count=len(values),excluded=total-len(values),
                average_seconds=fmean(values) if values else None,
                shortest_seconds=min(values) if values else None,
                mode_minutes=modes,mode_count=highest if modes else 0)


def callback_stats(rows):
    values={'workday':[],'after_hours':[]}; totals={'workday':0,'after_hours':0}
    for row in rows:
        group={'No':'workday','Yes':'after_hours'}.get(row.get('after_hours_lead'))
        if group is None: continue
        totals[group]+=1
        field='after_hours_response_seconds' if group=='after_hours' else 'speed_to_lead_seconds'
        seconds=row.get(field)
        if not parse_dt(row.get('first_called_at')): continue
        if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<0: continue
        values[group].append(seconds)
    return {'overall':_summary(values['workday']+values['after_hours'],len(rows)),
            **{group:_summary(values[group],totals[group]) for group in values}}


def render_callback_metrics(rows):
    import streamlit as st
    summaries=callback_stats(rows)
    st.subheader('Callback times')
    for group,title in [('overall','Overall · adjusted for after-hours leads'),
                        ('workday','Workday leads'),('after_hours','Overnight / after-hours leads')]:
        stats=summaries[group]
        st.caption(f"{title} · {stats['count']:,} of {stats['total']:,} leads with verified callbacks")
        a,b,c=st.columns(3)
        a.metric('Average callback',duration_label(stats['average_seconds']),help='Arithmetic mean of verified first-call response times, weighted equally per person.')
        b.metric('Shortest callback',duration_label(stats['shortest_seconds']))
        modes=stats['mode_minutes']
        mode='—' if not stats['count'] else 'No repeated range' if not modes else minute_range(modes[0]) if len(modes)==1 else f'Tie ({len(modes)} ranges)'
        detail='One-minute ranges: 5–<6 min means at least 5 minutes and less than 6. A mode is shown only when a range contains at least two callbacks.'
        if modes:
            detail+=' Most frequent: '+', '.join(minute_range(m) for m in modes[:10])+(' (additional tied ranges omitted)' if len(modes)>10 else '')+f"; {stats['mode_count']} callbacks per range."
        c.metric('Most frequent callback',mode,help=detail)
    st.caption('Workday leads use creation-to-first-outbound-call time. Overnight/after-hours leads use response after the next opening; calls before opening count as zero. Pending, uncalled, and unknown results are excluded. Metrics use everyone in this UTM tab, including people hidden by Only Show Matches, and update as lookup results arrive.')
