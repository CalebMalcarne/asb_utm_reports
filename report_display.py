"""Readable report dates and evidence-based speed to the first outbound attempt."""
from zoneinfo import ZoneInfo
from datetime import datetime,timezone
from core import parse_dt

DATE_FORMAT = '%m/%d/%Y %I:%M:%S%p'
DATE_COLUMNS = {'created_at', 'started_at', 'answered_at', 'ended_at', 'first_called_at',
                'activity_start', 'activity_end', 'fetched_at', 'aircall_fetched_at',
                'created', 'updated', 'next_run', 'next_workday_start','response_deadline','first_call_checked_through'}
SPEED_COLORS = {'Green': ('#15803d', '#ffffff'),
                'Yellow': ('#fef08a', '#172338'), 'Red': ('#b91c1c', '#ffffff')}


def format_timestamp(value, zone='UTC'):
    dt = parse_dt(value)
    return dt.astimezone(ZoneInfo(zone)).strftime(DATE_FORMAT) if dt else (value if value is not None else '')


def table_rows(rows, zone='UTC', include_timezone=False):
    out = []
    for row in rows:
        item = {k: format_timestamp(v, zone) if k in DATE_COLUMNS else v for k, v in row.items()}
        if include_timezone and DATE_COLUMNS.intersection(row):
            item['report_timezone'] = zone
        out.append(item)
    return out


def speed_to_lead(person, calls):
    result = dict(first_called_at=None, first_call_id=None, speed_to_lead_seconds=None,
                  speed_to_lead='Not fetched', speed_to_lead_color='', speed_to_lead_status='Not fetched')
    def unknown(reason):
        result.update(speed_to_lead=reason, speed_to_lead_status=reason)
        return result
    created = parse_dt(person.get('created_at'))
    start, end = parse_dt(person.get('activity_start')), parse_dt(person.get('activity_end'))
    status = person.get('aircall_status', 'not requested')
    if not created:
        return unknown('Unknown creation time')
    if status == 'not requested':
        return result
    if status != 'complete':
        return unknown('Unknown: '+status)
    if not start or not end or start > created or end <= created:
        return unknown('Unknown: history does not cover creation')
    result['first_call_checked_through']=min(end,datetime.now(timezone.utc)).isoformat()
    attempts = []
    for call in calls:
        if str(call.get('person_id')) != str(person['person_id']) or call.get('direction') != 'outbound':
            continue
        when = parse_dt(call.get('started_at'))
        if when and created <= when < end and when >= start:
            attempts.append((when, str(call['call_id'])))
    if not attempts:
        return unknown('No outbound call in fetched period')
    first, cid = min(attempts)
    elapsed = (first-created).total_seconds()
    color = 'Green' if elapsed <= 300 else 'Yellow' if elapsed <= 600 else 'Red'
    hours, rest = divmod(int(elapsed), 3600)
    minutes, seconds = divmod(rest, 60)
    result.update(first_called_at=first.isoformat(), first_call_id=cid, speed_to_lead_seconds=elapsed,
                  speed_to_lead=f'{hours}h {minutes:02d}m {seconds:02d}s',
                  speed_to_lead_color=color, speed_to_lead_status='First outbound attempt')
    return result


def people_table(rows, zone='UTC'):
    import pandas as pd
    frame = pd.DataFrame(table_rows(rows, zone))
    if frame.empty or 'speed_to_lead' not in frame:
        return frame
    front = [k for k in ('person_id','name','created_at','after_hours_lead','first_called_at','speed_to_lead',
                        'response_target','response_deadline','response_status','after_hours_response','next_workday_start') if k in frame]
    frame = frame[front+[k for k in frame if k not in front]]
    def shade(row):
        styles=[]
        for key in row.index:
            color_key={'speed_to_lead':'speed_to_lead_color','after_hours_response':'after_hours_response_color','response_status':'after_hours_response_color'}.get(key)
            if color_key:
                background,foreground=SPEED_COLORS.get(row.get(color_key),('#e5e7eb','#172338'))
                styles.append(f'background-color: {background}; color: {foreground}')
            elif key=='after_hours_lead' and row[key]=='Yes':
                styles.append('background-color: #dbeafe; color: #1e3a8a; font-weight: bold')
            else: styles.append('')
        return styles
    return frame.style.apply(shade, axis=1)
